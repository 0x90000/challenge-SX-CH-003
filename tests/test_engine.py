from decimal import Decimal

from psrc.engine import EngineConfig, SpotEngine

from .helpers import Case, manifest


def bar(t, price=100, symbol="SIM", low=None, high=None):
    return {
        "timestamp": t,
        "symbol": symbol,
        "open": price,
        "close": price,
        "low": price if low is None else low,
        "high": price if high is None else high,
    }


class EngineTests(Case):
    def engine(self, **config):
        m = manifest()
        m["actions"] = ["market", "limit", "target", "cancel"]
        return SpotEngine(m, EngineConfig(**config))

    def order(self, side="buy", quantity=2, **extra):
        return {"type": "market", "symbol": "SIM", "side": side, "quantity": quantity, **extra}

    def test_next_event_fee_slippage_and_exact_cash_conservation(self):
        engine = self.engine(initial_cash=1000, fee_rate=0.001, slippage=0.01)
        first = bar(1)
        engine.on_event(first)
        engine.submit([self.order()], first)
        self.assertEqual(engine.positions["SIM"], 0)
        self.assertEqual(engine.fills, [])
        engine.on_event(bar(2, 110))
        buy_cost = Decimal("111.1") * 2
        self.assertEqual(engine.cash, Decimal(1000) - buy_cost * Decimal("1.001"))
        engine.submit([self.order("sell")], bar(2, 110))
        engine.on_event(bar(3, 120))
        sell_value = Decimal("118.8") * 2
        self.assertEqual(
            engine.cash, Decimal(1000) - buy_cost * Decimal("1.001") + sell_value * Decimal("0.999")
        )
        self.assertEqual(engine.positions["SIM"], 0)
        self.assertEqual(engine.fees, (buy_cost + sell_value) * Decimal("0.001"))
        self.assertEqual([f["timestamp"] for f in engine.fills], [2, 3])

    def test_limits_cancel_expire_and_price_improvement(self):
        engine = self.engine()
        engine.on_event(bar(1))
        engine.submit([self.order(type="limit", price=99)], bar(1))
        engine.on_event(bar(2, 101))
        self.assertFalse(engine.fills)
        engine.on_event(bar(3, 98))
        self.assertEqual(Decimal(engine.fills[0]["price"]), 98)
        engine.submit([self.order("sell", type="limit", price=105)], bar(3, 98))
        engine.on_event(bar(4, 100, high=106))
        self.assertEqual(Decimal(engine.fills[1]["price"]), 105)
        engine.submit([self.order(type="limit", price=90)], bar(4))
        pending_id = engine.pending[0]["id"]
        engine.submit([{"type": "cancel", "symbol": "SIM", "order_id": pending_id}], bar(4))
        self.assertEqual(engine.orders[-1]["status"], "cancelled")
        engine.submit([self.order()], bar(4))
        report = engine.finish()
        self.assertEqual(report["orders"][-1]["status"], "expired")
        self.error(
            "ORDER_INVALID",
            engine.submit,
            [{"type": "cancel", "symbol": "SIM", "order_id": pending_id}],
            bar(5),
        )

    def test_risk_limits_and_pending_reservations(self):
        for order in (
            self.order(quantity=0.5),
            self.order("sell"),
            self.order(quantity=101),
            self.order(type="limit", price=99.001),
        ):
            engine = self.engine()
            engine.on_event(bar(1))
            self.error("ORDER_INVALID", engine.submit, [order], bar(1))
        engine = self.engine(initial_cash=350)
        engine.on_event(bar(1))
        engine.submit([self.order()], bar(1))
        self.error("ORDER_INVALID", engine.submit, [self.order()], bar(1))
        self.error(
            "ORDER_INVALID",
            engine.submit,
            [{"type": "target", "symbol": "SIM", "quantity": 4}],
            bar(1),
        )

    def test_gap_cannot_create_negative_cash_or_fake_fill(self):
        engine = self.engine(initial_cash=250)
        engine.on_event(bar(1))
        engine.submit([self.order()], bar(1))
        self.error("BACKTEST_FAILED", engine.on_event, bar(2, 200))
        self.assertEqual(engine.cash, 250)
        self.assertEqual(engine.positions["SIM"], 0)
        self.assertFalse(engine.fills)

    def test_tick_quotes_and_symbol_event_order(self):
        engine = self.engine(fee_rate=0, slippage=0)
        engine.manifest["granularity"] = "tick"
        event = {"timestamp": 1, "symbol": "SIM", "bid": 99, "ask": 101}
        engine.on_event(event)
        engine.submit([self.order()], event)
        engine.on_event({**event, "timestamp": 2, "ask": 102})
        self.assertEqual(Decimal(engine.fills[0]["price"]), 102)
        self.error("BACKTEST_FAILED", engine.on_event, event)
        self.error("BACKTEST_FAILED", engine.on_event, {**event, "symbol": "UNKNOWN"})

    def test_target_noop_and_snapshot_is_a_copy(self):
        engine = self.engine()
        engine.on_event(bar(1))
        engine.submit([{"type": "target", "symbol": "SIM", "quantity": 0}], bar(1))
        self.assertFalse(engine.orders)
        engine.submit([self.order()], bar(1))
        snapshot = engine.snapshot()
        snapshot["open_orders"][0]["quantity"] = 999
        snapshot["positions"]["SIM"] = 999
        self.assertEqual(engine.pending[0]["quantity"], 2)
        self.assertEqual(engine.positions["SIM"], 0)
