"""Deterministic long-only spot engine. Orders can fill only on later symbol events."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from .contract import keys, number, validate_orders
from .errors import ContractError, require


def dec(value):
    return Decimal(str(value))


@dataclass(frozen=True)
class EngineConfig:
    initial_cash: float = 100_000.0
    fee_rate: float = 0.0005
    slippage: float = 0.0002
    lot_size: float = 1.0
    tick_size: float = 0.01
    max_position: float = 100.0

    def __post_init__(self):
        for name in ("initial_cash", "lot_size", "tick_size", "max_position"):
            number(getattr(self, name), positive=True)
        for name in ("fee_rate", "slippage"):
            number(getattr(self, name), minimum=0)
            require(getattr(self, name) < 1, "SCHEMA_INVALID", f"{name} must be < 1")

    @classmethod
    def from_dict(cls, value):
        keys(value, (), cls.__dataclass_fields__)
        return cls(**value)


class BacktestEngine(Protocol):
    """Trusted adapter surface; strategies never receive this object."""

    def on_event(self, event: dict) -> None: ...
    def submit(self, orders: list, event: dict) -> None: ...
    def snapshot(self) -> dict: ...
    def finish(self) -> dict: ...


class SpotEngine:
    def __init__(self, manifest, config):
        self.manifest, self.config = manifest, config
        self.cash = dec(config.initial_cash)
        self.positions = {symbol: Decimal(0) for symbol in manifest["symbols"]}
        self.marks = {}
        self.orders = []
        self.pending = []
        self.fills = []
        self.equity = []
        self.fees = Decimal(0)
        self._last = {}

    def snapshot(self):
        return {
            "cash": float(self.cash),
            "positions": {s: float(q) for s, q in self.positions.items()},
            "equity": float(self.cash + sum(self.positions[s] * p for s, p in self.marks.items())),
            "open_orders": [dict(o) for o in self.pending],
        }

    def _quantity(self, quantity):
        q = dec(quantity)
        require(
            q % dec(self.config.lot_size) == 0,
            "ORDER_INVALID",
            "Quantity violates lot size",
            quantity=quantity,
            lot_size=self.config.lot_size,
        )
        return q

    def submit(self, orders, event):
        validate_orders(orders, self.manifest)
        for request in orders:
            symbol, kind = request["symbol"], request["type"]
            if kind == "cancel":
                found = next(
                    (
                        o
                        for o in self.pending
                        if o["id"] == request["order_id"] and o["symbol"] == symbol
                    ),
                    None,
                )
                require(
                    found is not None,
                    "ORDER_INVALID",
                    "Cannot cancel unknown/closed order",
                    order_id=request["order_id"],
                )
                found["status"] = "cancelled"
                found["closed_at"] = event["timestamp"]
                self.pending.remove(found)
                continue
            qty = self._quantity(request["quantity"])
            if kind == "target":
                require(
                    not any(o["symbol"] == symbol for o in self.pending),
                    "ORDER_INVALID",
                    "Target position is ambiguous while orders are pending",
                )
                difference = qty - self.positions[symbol]
                if difference == 0:
                    continue
                side, qty, actual_kind = (
                    ("buy" if difference > 0 else "sell"),
                    abs(difference),
                    "market",
                )
            else:
                side, actual_kind = request["side"], kind
            if actual_kind == "limit":
                require(
                    dec(request["price"]) % dec(self.config.tick_size) == 0,
                    "ORDER_INVALID",
                    "Limit price violates tick size",
                )
            buys = sum(
                dec(o["quantity"])
                for o in self.pending
                if o["symbol"] == symbol and o["side"] == "buy"
            )
            sells = sum(
                dec(o["quantity"])
                for o in self.pending
                if o["symbol"] == symbol and o["side"] == "sell"
            )
            require(
                side != "sell" or qty <= self.positions[symbol] - sells,
                "ORDER_INVALID",
                "Short sales and overselling are forbidden",
            )
            require(
                side != "buy"
                or self.positions[symbol] + buys + qty <= dec(self.config.max_position),
                "ORDER_INVALID",
                "Position limit exceeded",
            )
            require(symbol in self.marks, "ORDER_INVALID", "No price observed for order symbol")
            estimate = (
                dec(request["price"])
                if actual_kind == "limit"
                else self.marks[symbol] * (1 + dec(self.config.slippage))
            )
            reserved = sum(dec(o["reserved_cash"]) for o in self.pending)
            cost = qty * estimate * (1 + dec(self.config.fee_rate)) if side == "buy" else Decimal(0)
            require(cost <= self.cash - reserved, "ORDER_INVALID", "Insufficient unreserved cash")
            order = {
                "id": f"o{len(self.orders) + 1:06d}",
                "symbol": symbol,
                "side": side,
                "type": actual_kind,
                "requested_type": kind,
                "quantity": float(qty),
                "submitted_at": event["timestamp"],
                "status": "open",
                "reserved_cash": str(cost),
            }
            if actual_kind == "limit":
                order["price"] = request["price"]
            self.orders.append(order)
            self.pending.append(order)

    def on_event(self, event):
        symbol, timestamp = event["symbol"], event["timestamp"]
        require(
            symbol in self.positions and timestamp > self._last.get(symbol, -1),
            "BACKTEST_FAILED",
            "Engine received unknown or non-monotonic event",
        )
        self._last[symbol] = timestamp
        tick = self.manifest["granularity"] == "tick"
        self.marks[symbol] = (
            (dec(event["bid"]) + dec(event["ask"])) / 2 if tick else dec(event["close"])
        )
        for order in list(self.pending):
            if order["symbol"] != symbol or order["submitted_at"] >= timestamp:
                continue
            buy = order["side"] == "buy"
            base = dec(event["ask"] if buy else event["bid"]) if tick else dec(event["open"])
            if order["type"] == "limit":
                limit = dec(order["price"])
                touch = base if tick else dec(event["low"] if buy else event["high"])
                if (buy and touch > limit) or (not buy and touch < limit):
                    continue
                price = min(base, limit) if buy else max(base, limit)
            else:
                price = base * (1 + dec(self.config.slippage) * (1 if buy else -1))
            quantity = dec(order["quantity"])
            notional = price * quantity
            fee = notional * dec(self.config.fee_rate)
            if buy and notional + fee > self.cash:
                raise ContractError(
                    "BACKTEST_FAILED",
                    "Price gap caused insufficient cash",
                    stage="backtest",
                    details={"order_id": order["id"]},
                )
            require(
                buy or quantity <= self.positions[symbol],
                "BACKTEST_FAILED",
                "Fill would create a negative spot position",
            )
            self.cash += -notional - fee if buy else notional - fee
            self.positions[symbol] += quantity if buy else -quantity
            self.fees += fee
            order["status"], order["closed_at"] = "filled", timestamp
            self.pending.remove(order)
            self.fills.append(
                {
                    "order_id": order["id"],
                    "symbol": symbol,
                    "timestamp": timestamp,
                    "side": order["side"],
                    "quantity": str(quantity),
                    "price": str(price),
                    "fee": str(fee),
                    "cash_after": str(self.cash),
                    "position_after": str(self.positions[symbol]),
                }
            )
        equity = self.cash + sum(self.positions[s] * p for s, p in self.marks.items())
        self.equity.append({"timestamp": timestamp, "symbol": symbol, "value": str(equity)})

    def finish(self):
        for order in self.pending:
            order["status"] = "expired"
            order["reason"] = "end_of_data"
        self.pending.clear()
        final = dec(self.snapshot()["equity"]) if not self.equity else dec(self.equity[-1]["value"])
        peak, drawdown = dec(self.config.initial_cash), Decimal(0)
        for point in self.equity:
            value = dec(point["value"])
            peak = max(peak, value)
            drawdown = max(drawdown, (peak - value) / peak)
        return {
            "initial_cash": str(dec(self.config.initial_cash)),
            "cash": str(self.cash),
            "positions": {s: str(q) for s, q in self.positions.items()},
            "equity": str(final),
            "pnl": str(final - dec(self.config.initial_cash)),
            "return": float(final / dec(self.config.initial_cash) - 1),
            "max_drawdown": float(drawdown),
            "fees": str(self.fees),
            "orders": self.orders,
            "fills": self.fills,
            "equity_curve": self.equity,
            "assumptions": {
                "account": "long-only spot",
                "fill": "next symbol event",
                "liquidity": "full fills; no queue or market-impact model",
                "limit": "touch fill; no intrabar path inference",
                "valuation": "mark to market; no forced liquidation",
            },
        }
