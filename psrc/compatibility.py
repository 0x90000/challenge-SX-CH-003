"""Opt-in lossless renaming. No implicit filling, resampling or price invention."""

from copy import deepcopy

from .codec import digest
from .contract import keys
from .errors import require


def convert(data, policy, audit):
    if policy is None:
        return data
    keys(policy, {"enabled", "field_aliases", "symbol_map", "reason"})
    require(type(policy["enabled"]) is bool, "COMPATIBILITY_INVALID", "enabled must be bool")
    require(
        isinstance(policy["reason"], str) and bool(policy["reason"]),
        "COMPATIBILITY_INVALID",
        "Conversion requires an explicit reason",
    )
    for name in ("field_aliases", "symbol_map"):
        mapping = policy[name]
        require(
            isinstance(mapping, dict)
            and all(
                isinstance(k, str) and isinstance(v, str) and k and v and k != v
                for k, v in mapping.items()
            ),
            "COMPATIBILITY_INVALID",
            "Mappings must contain distinct nonempty strings",
        )
        require(
            len(set(mapping.values())) == len(mapping),
            "COMPATIBILITY_INVALID",
            "Many-to-one mappings are forbidden",
        )
    record = {
        "component": "explicit-renaming/1.0",
        "enabled": policy["enabled"],
        "reason": policy["reason"],
        "can_disable": True,
        "policy": policy,
        "input_sha256": digest(data),
        "changes": [],
    }
    audit.append(record)
    if not policy["enabled"]:
        record["output_sha256"] = record["input_sha256"]
        return data
    keys(data, {"granularity", "rows", "provenance"})
    require(isinstance(data["rows"], list), "DATA_INVALID", "rows must be list")
    result = deepcopy(data)
    symbols = {
        r.get("symbol")
        for r in data["rows"]
        if isinstance(r, dict) and isinstance(r.get("symbol"), str)
    }
    # Each rename is recorded with its exact row and values, even if later validation fails.
    for index, row in enumerate(result["rows"]):
        require(isinstance(row, dict), "DATA_INVALID", "Event must be an object")
        for old, new in policy["field_aliases"].items():
            require(
                old not in {"timestamp", "symbol"} and new not in {"timestamp", "symbol"},
                "COMPATIBILITY_INVALID",
                "Identity fields cannot be renamed",
            )
            if old in row:
                require(
                    new not in row,
                    "COMPATIBILITY_CONFLICT",
                    "Rename would overwrite field",
                    row=index,
                    source=old,
                    target=new,
                )
                row[new] = row.pop(old)
                record["changes"].append({"row": index, "kind": "field", "from": old, "to": new})
        symbol = row.get("symbol")
        require(isinstance(symbol, str), "DATA_INVALID", "symbol must be a string")
        if symbol in policy["symbol_map"]:
            mapped = policy["symbol_map"][symbol]
            require(
                mapped not in symbols,
                "COMPATIBILITY_CONFLICT",
                "Symbol mapping collides with existing symbol",
            )
            row["symbol"] = mapped
            record["changes"].append({"row": index, "kind": "symbol", "from": symbol, "to": mapped})
    record["output_sha256"] = digest(result)
    record["affected_rows"] = len({c["row"] for c in record["changes"]})
    return result
