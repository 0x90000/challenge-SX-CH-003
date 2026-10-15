"""Strict v1 wire contract. Unknown values are errors, never inferred defaults."""

import math
import re
from pathlib import Path

from .codec import digest, read_json
from .errors import ContractError, require

VERSION = "1.0"
GRANULARITIES = {"tick": None, "1m": 60_000, "1d": 86_400_000}
FIELDS = {
    "open",
    "high",
    "low",
    "close",
    "volume",
    "bid",
    "ask",
    "bid_size",
    "ask_size",
    "bids",
    "asks",
    "trade_price",
    "trade_size",
}
KINDS = {"rule", "supervised", "rl"}
ACTIONS = {"market", "limit", "cancel", "target"}


def keys(value, required, optional=(), *, code="SCHEMA_INVALID"):
    require(isinstance(value, dict), code, "Expected an object")
    missing = set(required) - value.keys()
    extra = value.keys() - set(required) - set(optional)
    require(
        not missing and not extra,
        code,
        "Object keys do not match contract",
        missing=sorted(missing),
        unknown=sorted(extra),
    )


def number(value, *, minimum=None, positive=False, code="SCHEMA_INVALID"):
    try:
        valid = type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        valid = False
    require(valid, code, "Expected a finite number", value=repr(value)[:80])
    require(not positive or value > 0, code, "Number must be positive")
    require(minimum is None or value >= minimum, code, "Number below minimum", minimum=minimum)
    return value


def strings(value, *, nonempty=True):
    require(
        isinstance(value, list) and (not nonempty or bool(value)),
        "SCHEMA_INVALID",
        "Expected a nonempty string list",
    )
    require(
        all(isinstance(v, str) and bool(v) for v in value),
        "SCHEMA_INVALID",
        "Expected nonempty strings",
    )
    require(len(set(value)) == len(value), "SCHEMA_INVALID", "Duplicate list entries")


def validate_manifest(m):
    keys(
        m,
        {
            "contract_version",
            "id",
            "kind",
            "granularity",
            "fields",
            "symbols",
            "lookback",
            "actions",
            "description",
            "reference",
        },
        {"config"},
    )
    require(
        m["contract_version"] == VERSION,
        "VERSION_UNSUPPORTED",
        "Unsupported contract",
        supported=[VERSION],
        actual=m["contract_version"],
    )
    require(
        isinstance(m["id"], str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", m["id"]),
        "SCHEMA_INVALID",
        "Invalid strategy id",
    )
    require(
        isinstance(m["kind"], str) and m["kind"] in KINDS, "SCHEMA_INVALID", "Unknown strategy kind"
    )
    require(
        isinstance(m["granularity"], str) and m["granularity"] in GRANULARITIES,
        "GRANULARITY_MISMATCH",
        "Unsupported granularity",
    )
    for name in ("fields", "symbols", "actions"):
        strings(m[name])
    require(
        set(m["fields"]) <= FIELDS,
        "FIELD_UNKNOWN",
        "Unknown field requested",
        unknown=sorted(set(m["fields"]) - FIELDS),
    )
    require(set(m["actions"]) <= ACTIONS, "ACTION_INVALID", "Unknown action declared")
    require(
        type(m["lookback"]) is int and 1 <= m["lookback"] <= 1024,
        "SCHEMA_INVALID",
        "lookback must be an integer in [1, 1024]",
    )
    for name in ("description", "reference"):
        require(
            isinstance(m[name], str) and bool(m[name]),
            "SCHEMA_INVALID",
            f"{name} must be a nonempty string",
        )
    require(isinstance(m.get("config", {}), dict), "SCHEMA_INVALID", "config must be object")
    return m


def load_strategy(directory):
    directory = Path(directory)
    for filename in ("manifest.json", "strategy.py"):
        path = directory / filename
        require(
            not path.is_symlink() and path.resolve().parent == directory.resolve(),
            "SANDBOX_VIOLATION",
            "Strategy files must be regular local files",
        )
    manifest = validate_manifest(read_json(directory / "manifest.json"))
    try:
        with (directory / "strategy.py").open("rb") as stream:
            raw = stream.read(128_001)
        require(len(raw) <= 128_000, "RESOURCE_LIMIT", "Strategy source too large")
        source = raw.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise ContractError(
            "STRATEGY_LOAD_FAILED",
            "Cannot load strategy source",
            details={"cause": type(exc).__name__},
        ) from exc
    return manifest, source, digest({"manifest": manifest, "source": source})


def validate_data(data, manifest):
    keys(data, {"granularity", "rows", "provenance"})
    require(
        data["granularity"] == manifest["granularity"],
        "GRANULARITY_MISMATCH",
        "Input resolution differs from strategy declaration",
        expected=manifest["granularity"],
        actual=data["granularity"],
    )
    require(
        isinstance(data["provenance"], str) and bool(data["provenance"]),
        "SCHEMA_INVALID",
        "Data provenance is required",
    )
    rows = data["rows"]
    require(isinstance(rows, list) and bool(rows), "DATA_INVALID", "No market events")
    previous = {}
    counts = {}
    last_global = -1
    step = GRANULARITIES[manifest["granularity"]]
    for index, row in enumerate(rows):
        require(isinstance(row, dict), "DATA_INVALID", "Event must be object", row=index)
        missing = {"timestamp", "symbol", *manifest["fields"]} - row.keys()
        require(
            not missing,
            "FIELD_MISSING",
            "Required market fields are missing",
            row=index,
            fields=sorted(missing),
        )
        require(
            not (row.keys() - FIELDS - {"timestamp", "symbol"}),
            "FIELD_UNKNOWN",
            "Unknown input fields",
            row=index,
        )
        ts, symbol = row["timestamp"], row["symbol"]
        require(type(ts) is int and ts >= 0, "DATA_INVALID", "timestamp must be UTC ms")
        require(
            isinstance(symbol, str) and symbol in manifest["symbols"],
            "SYMBOL_UNMAPPED",
            "Input symbol is not declared",
            symbol=symbol,
            row=index,
        )
        require(
            ts >= last_global and ts > previous.get(symbol, -1),
            "DATA_INVALID",
            "Events must be chronological and unique per symbol",
            row=index,
        )
        require(
            step is None or ts % step == 0,
            "GRANULARITY_MISMATCH",
            "Bar close timestamp is not on the declared UTC boundary",
            row=index,
        )
        previous[symbol], last_global = ts, ts
        counts[symbol] = counts.get(symbol, 0) + 1
        for field, value in row.items():
            if field in ("timestamp", "symbol"):
                continue
            if field in ("bids", "asks"):
                require(
                    isinstance(value, list) and 1 <= len(value) <= 100,
                    "DATA_INVALID",
                    "Book must have 1..100 levels",
                    row=index,
                )
                prices = []
                for level in value:
                    require(
                        isinstance(level, list) and len(level) == 2,
                        "DATA_INVALID",
                        "Book level must be [price, size]",
                        row=index,
                    )
                    number(level[0], positive=True, code="DATA_INVALID")
                    number(level[1], minimum=0, code="DATA_INVALID")
                    prices.append(level[0])
                require(
                    all((a > b if field == "bids" else a < b) for a, b in zip(prices, prices[1:])),
                    "DATA_INVALID",
                    "Book levels are not strictly sorted",
                    row=index,
                )
            else:
                number(
                    value,
                    minimum=0,
                    positive=field in {"open", "high", "low", "close", "bid", "ask", "trade_price"},
                    code="DATA_INVALID",
                )
        if {"open", "high", "low", "close"} <= row.keys():
            require(
                row["low"]
                <= min(row["open"], row["close"])
                <= max(row["open"], row["close"])
                <= row["high"],
                "DATA_INVALID",
                "OHLC range is inconsistent",
                row=index,
            )
        if {"bid", "ask"} <= row.keys():
            require(row["bid"] <= row["ask"], "DATA_INVALID", "Crossed L1 book", row=index)
        if {"bids", "asks"} <= row.keys():
            require(
                row["bids"][0][0] <= row["asks"][0][0], "DATA_INVALID", "Crossed L2 book", row=index
            )
            for side, field in (("bids", "bid"), ("asks", "ask")):
                if field in row:
                    require(
                        row[side][0][0] == row[field],
                        "DATA_INVALID",
                        "L1 and L2 top prices differ",
                        row=index,
                    )
        execution = {"bid", "ask"} if step is None else {"open", "high", "low", "close"}
        require(
            execution <= row.keys(),
            "FIELD_MISSING",
            "Engine execution fields missing",
            fields=sorted(execution - row.keys()),
            row=index,
        )
    require(
        set(counts) == set(manifest["symbols"]),
        "SYMBOL_UNMAPPED",
        "Not all declared symbols have market events",
    )
    return rows


def validate_orders(orders, manifest):
    require(
        isinstance(orders, list) and len(orders) <= 32,
        "ACTION_INVALID",
        "Expected at most 32 orders per event",
    )
    for order in orders:
        require(isinstance(order, dict), "ACTION_INVALID", "Order must be object")
        kind = order.get("type")
        require(
            isinstance(kind, str) and kind in manifest["actions"],
            "ACTION_INVALID",
            "Action is not declared",
            action=kind,
        )
        common = {"type", "symbol"}
        if kind == "cancel":
            keys(order, common | {"order_id"}, code="ACTION_INVALID")
            require(
                isinstance(order["order_id"], str) and bool(order["order_id"]),
                "ACTION_INVALID",
                "cancel needs an order id",
            )
        elif kind == "target":
            keys(order, common | {"quantity"}, code="ACTION_INVALID")
            number(order["quantity"], minimum=0, code="ACTION_INVALID")
        else:
            required = common | {"side", "quantity"}
            if kind == "limit":
                required |= {"price"}
            keys(order, required, code="ACTION_INVALID")
            require(order["side"] in ("buy", "sell"), "ACTION_INVALID", "Invalid order side")
            number(order["quantity"], positive=True, code="ACTION_INVALID")
            if kind == "limit":
                number(order["price"], positive=True, code="ACTION_INVALID")
        require(
            isinstance(order["symbol"], str) and order["symbol"] in manifest["symbols"],
            "SYMBOL_UNMAPPED",
            "Order symbol is not declared",
        )
    return orders
