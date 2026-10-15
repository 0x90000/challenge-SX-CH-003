"""Publish JSON Schema 2020-12 wire definitions; semantic checks live in contract.py."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from psrc.codec import write_json  # noqa: E402
from psrc.contract import FIELDS  # noqa: E402


def obj(properties, required=None, extra=False):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties) if required is None else required,
        "additionalProperties": extra,
    }


def ref(name):
    return {"$ref": f"#/$defs/{name}"}


def main():
    text = {"type": "string", "minLength": 1}
    positive = {"type": "number", "exclusiveMinimum": 0}
    nonnegative = {"type": "number", "minimum": 0}
    integer = {"type": "integer", "minimum": 0}
    sha = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
    strings = {"type": "array", "items": text, "minItems": 1, "uniqueItems": True}
    granularity = {"enum": ["tick", "1m", "1d"]}
    definitions = {}
    definitions["Manifest"] = obj(
        {
            "contract_version": {"const": "1.0"},
            "id": {"type": "string", "pattern": "^[a-z][a-z0-9_]{0,63}$"},
            "kind": {"enum": ["rule", "supervised", "rl"]},
            "granularity": granularity,
            "fields": {**strings, "items": {"enum": sorted(FIELDS)}},
            "symbols": strings,
            "lookback": {"type": "integer", "minimum": 1, "maximum": 1024},
            "actions": {**strings, "items": {"enum": ["market", "limit", "target", "cancel"]}},
            "description": text,
            "reference": text,
            "config": {"type": "object"},
        },
        required=[
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
        ],
    )
    level = {"type": "array", "prefixItems": [positive, nonnegative], "minItems": 2, "maxItems": 2}
    book = {"type": "array", "items": level, "minItems": 1, "maxItems": 100}
    fields = {
        name: positive for name in ("open", "high", "low", "close", "bid", "ask", "trade_price")
    }
    fields.update({name: nonnegative for name in ("volume", "bid_size", "ask_size", "trade_size")})
    fields.update(bids=book, asks=book, timestamp=integer, symbol=text)
    definitions["Event"] = obj(fields, required=["timestamp", "symbol"])
    definitions["MarketData"] = obj(
        {
            "granularity": granularity,
            "provenance": text,
            "rows": {"type": "array", "items": ref("Event"), "minItems": 1},
        }
    )
    definitions["Order"] = {
        "oneOf": [
            obj(
                {
                    "type": {"const": "market"},
                    "symbol": text,
                    "side": {"enum": ["buy", "sell"]},
                    "quantity": positive,
                }
            ),
            obj(
                {
                    "type": {"const": "limit"},
                    "symbol": text,
                    "side": {"enum": ["buy", "sell"]},
                    "quantity": positive,
                    "price": positive,
                }
            ),
            obj({"type": {"const": "target"}, "symbol": text, "quantity": nonnegative}),
            obj({"type": {"const": "cancel"}, "symbol": text, "order_id": text}),
        ]
    }
    definitions["Inference"] = obj(
        {
            "orders": {"type": "array", "items": ref("Order"), "maxItems": 32},
            "state": {"type": "object"},
            "diagnostics": {"type": "object"},
        }
    )
    definitions["Account"] = obj(
        {
            "cash": nonnegative,
            "equity": {"type": "number"},
            "positions": {"type": "object", "additionalProperties": nonnegative},
            "open_orders": {"type": "array", "items": {"type": "object"}},
        }
    )
    definitions["Model"] = obj(
        {
            "contract_version": {"const": "1.0"},
            "strategy_id": text,
            "strategy_sha256": sha,
            "sdk_sha256": sha,
            "config_sha256": sha,
            "training_data_sha256": sha,
            "training_end": integer,
            "training_rows": {"type": "integer", "minimum": 1},
            "payload": {"type": "object", "minProperties": 1},
            "payload_sha256": sha,
            "envelope_sha256": sha,
        }
    )
    definitions["Error"] = obj(
        {
            "code": {"type": "string", "pattern": "^[A-Z_]+$"},
            "stage": text,
            "message": {"type": "string"},
            "details": {"type": "object"},
            "retryable": {"type": "boolean"},
        }
    )
    nullable_object = {"type": ["object", "null"]}
    properties = {
        "contract_version": {"const": "1.0"},
        "runtime_version": text,
        "status": {"enum": ["success", "failed"]},
        "mode": text,
        "environment": {"type": "object"},
        "strategy": {"anyOf": [ref("Manifest"), {"type": "null"}]},
        "input": nullable_object,
        "training": nullable_object,
        "artifacts": {"type": "object"},
        "lifecycle": {"type": "array", "items": text},
        "compatibility": {"type": "array", "items": {"type": "object"}},
        "inference": {"type": "array", "items": {"type": "object"}},
        "backtest": nullable_object,
        "error": {"anyOf": [ref("Error"), {"type": "null"}]},
        "sandbox": {"type": "object"},
        "elapsed_seconds": nonnegative,
        "result_sha256": sha,
        "engine_config": {"type": "object"},
        "strategy_sha256": sha,
        "loaded_model_sha256": sha,
        "partial_account": ref("Account"),
    }
    definitions["Report"] = obj(
        properties,
        required=[
            k
            for k in properties
            if k
            not in {"engine_config", "strategy_sha256", "loaded_model_sha256", "partial_account"}
        ],
    )
    definitions["Report"]["allOf"] = [
        {
            "if": {"properties": {"status": {"const": "success"}}},
            "then": {"properties": {"error": {"type": "null"}}},
            "else": {"properties": {"error": ref("Error")}},
        }
    ]
    for name in definitions:
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": f"PSRC v1 {name}",
            "$ref": f"#/$defs/{name}",
            "$defs": definitions,
        }
        write_json(ROOT / "schemas" / f"{name.lower()}.schema.json", schema)


if __name__ == "__main__":
    main()
