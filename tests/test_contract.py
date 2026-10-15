from copy import deepcopy

from psrc.codec import decode, digest, encode, read_json, write_json
from psrc.compatibility import convert
from psrc.contract import load_strategy, validate_data, validate_manifest, validate_orders
from psrc.data import synthetic
from psrc.engine import EngineConfig

from .helpers import Case, manifest, market


class ContractTests(Case):
    def test_json_rejects_duplicate_nonfinite_and_invalid_unicode(self):
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b"\xff", b"["):
            with self.subTest(raw=raw):
                self.error("SCHEMA_INVALID", decode, raw)
        for value in (float("nan"), {"x": object()}):
            self.error("SCHEMA_INVALID", encode, value)
        value = {"nested": [1, True, None, "中文"]}
        write_json(self.path / "deep/file.json", value)
        self.assertEqual(read_json(self.path / "deep/file.json"), value)
        self.assertEqual(digest({"a": 1, "b": 2}), digest({"b": 2, "a": 1}))
        self.error("MODEL_MISSING", read_json, self.path / "absent", "MODEL_MISSING")

    def test_manifest_strict_version_and_requirements(self):
        cases = [
            ("contract_version", "2.0", "VERSION_UNSUPPORTED"),
            ("fields", ["secret"], "FIELD_UNKNOWN"),
            ("fields", ["close", "close"], "SCHEMA_INVALID"),
            ("lookback", True, "SCHEMA_INVALID"),
            ("granularity", "hour", "GRANULARITY_MISMATCH"),
            ("actions", ["teleport"], "ACTION_INVALID"),
            ("id", "../../escape", "SCHEMA_INVALID"),
            ("kind", [], "SCHEMA_INVALID"),
            ("config", [], "SCHEMA_INVALID"),
        ]
        for key, value, code in cases:
            with self.subTest(key=key, value=value):
                m = manifest()
                m[key] = value
                self.error(code, validate_manifest, m)
        m = manifest()
        m["unknown"] = 1
        self.error("SCHEMA_INVALID", validate_manifest, m)
        directory = self.strategy()
        self.assertEqual(load_strategy(directory)[0], manifest())
        (directory / "strategy.py").unlink()
        self.error("STRATEGY_LOAD_FAILED", load_strategy, directory)
        (directory / "strategy.py").write_bytes(b" " * 128_001)
        self.error("RESOURCE_LIMIT", load_strategy, directory)

    def test_data_failures_preserve_specific_codes(self):
        mutations = [
            (lambda d: d["rows"][0].pop("close"), "FIELD_MISSING"),
            (lambda d: d.update(granularity="tick"), "GRANULARITY_MISMATCH"),
            (lambda d: d["rows"][0].update(symbol="UNKNOWN"), "SYMBOL_UNMAPPED"),
            (lambda d: d["rows"][0].update(close=float("nan")), "DATA_INVALID"),
            (lambda d: d["rows"][0].update(close=True), "DATA_INVALID"),
            (lambda d: d["rows"][0].update(high=1), "DATA_INVALID"),
            (lambda d: d["rows"][0].update(bid=200), "DATA_INVALID"),
            (lambda d: d["rows"][0].update(volume=-1), "DATA_INVALID"),
            (lambda d: d["rows"][0].update(bids=[[100, 1], [101, 1]]), "DATA_INVALID"),
            (lambda d: d["rows"][0].update(asks=[]), "DATA_INVALID"),
            (lambda d: d["rows"][0].update(bids=[[1]]), "DATA_INVALID"),
            (lambda d: d["rows"][0].update(timestamp=1), "GRANULARITY_MISMATCH"),
            (lambda d: d["rows"].reverse(), "DATA_INVALID"),
            (lambda d: d["rows"].append(d["rows"][-1]), "DATA_INVALID"),
            (lambda d: d["rows"][0].update(rogue=1), "FIELD_UNKNOWN"),
            (lambda d: d.update(rows=[]), "DATA_INVALID"),
        ]
        for mutation, code in mutations:
            with self.subTest(code=code, mutation=mutation):
                data = market()
                mutation(data)
                self.error(code, validate_data, data, manifest())

    def test_tick_daily_minute_and_multi_symbol_time_contract(self):
        for granularity in ("tick", "1m", "1d"):
            m = manifest()
            m.update(
                granularity=granularity,
                fields=["bid", "ask"] if granularity == "tick" else ["close"],
            )
            self.assertEqual(len(validate_data(synthetic(granularity, 30), m)), 30)
        data = market()
        other = deepcopy(data["rows"])
        for row in other:
            row["symbol"] = "SECOND"
        data["rows"] = sorted(data["rows"] + other, key=lambda row: row["timestamp"])
        m = manifest()
        m["symbols"].append("SECOND")
        self.assertEqual(len(validate_data(data, m)), 160)

    def test_orders_and_config_reject_nan_bool_negative_unknown(self):
        m = manifest()
        m["actions"] = ["market", "limit", "cancel", "target"]
        valid = {"type": "market", "symbol": "SIM", "side": "buy", "quantity": 1}
        for change, code in [
            ({"quantity": 0}, "ACTION_INVALID"),
            ({"quantity": True}, "ACTION_INVALID"),
            ({"quantity": float("inf")}, "ACTION_INVALID"),
            ({"side": "short"}, "ACTION_INVALID"),
            ({"symbol": "BAD"}, "SYMBOL_UNMAPPED"),
            ({"type": []}, "ACTION_INVALID"),
        ]:
            self.error(code, validate_orders, [{**valid, **change}], m)
        self.error("ACTION_INVALID", validate_orders, [valid] * 33, m)
        self.error(
            "ACTION_INVALID",
            validate_orders,
            [{"type": "cancel", "symbol": "SIM", "order_id": 1}],
            m,
        )
        for config in ({"fee_rate": -1}, {"slippage": 1}, {"lot_size": 0}, {"mystery": 1}):
            self.error("SCHEMA_INVALID", EngineConfig.from_dict, config)


class CompatibilityTests(Case):
    def policy(self, enabled=True):
        return {
            "enabled": enabled,
            "field_aliases": {"closing": "close"},
            "symbol_map": {"VENDOR": "SIM"},
            "reason": "Documented vendor mapping",
        }

    def test_conversion_is_explicit_audited_and_does_not_mutate(self):
        data = market()
        for row in data["rows"]:
            row["closing"] = row.pop("close")
            row["symbol"] = "VENDOR"
        original = deepcopy(data)
        audit = []
        result = convert(data, self.policy(), audit)
        validate_data(result, manifest())
        self.assertEqual(data, original)
        self.assertEqual(audit[0]["affected_rows"], 80)
        self.assertEqual(len(audit[0]["changes"]), 160)
        self.assertNotEqual(audit[0]["input_sha256"], audit[0]["output_sha256"])
        disabled = []
        self.assertIs(convert(data, self.policy(False), disabled), data)
        self.error("FIELD_MISSING", validate_data, data, manifest())
        self.assertFalse(disabled[0]["enabled"])
        self.assertIs(convert(data, None, []), data)

    def test_alias_collisions_and_invalid_policy_fail(self):
        data = market()
        data["rows"][0]["closing"] = 1
        audit = []
        self.error("COMPATIBILITY_CONFLICT", convert, data, self.policy(), audit)
        self.assertEqual(len(audit), 1)
        for change in ({"enabled": 1}, {"reason": ""}, {"field_aliases": {"a": "x", "b": "x"}}):
            self.error("COMPATIBILITY_INVALID", convert, data, {**self.policy(), **change}, [])
