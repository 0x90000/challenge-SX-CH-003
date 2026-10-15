from copy import deepcopy
from decimal import Decimal

from psrc.codec import digest, read_json, write_json
from psrc.engine import SpotEngine
from psrc.runtime import load_model, run
from psrc.contract import load_strategy

from .helpers import ROOT, SOURCE, Case, market


class RuntimeTests(Case):
    def execute(self, **kwargs):
        return run(self.strategy(), market(), self.path / "out", **kwargs)

    def test_all_18_distinct_strategies_train_reload_and_backtest(self):
        counts = {"rule": 0, "supervised": 0, "rl": 0}
        source_hashes, algorithms = set(), set()
        for directory in sorted((ROOT / "examples").iterdir()):
            with self.subTest(strategy=directory.name):
                m = read_json(directory / "manifest.json")
                report = run(
                    directory,
                    ROOT / "data" / f"{m['granularity']}.json",
                    self.path / directory.name,
                )
                self.assertEqual(report["status"], "success", report["error"])
                self.assertEqual(
                    report["lifecycle"],
                    [
                        "initialized_for_training",
                        "trained",
                        "saved",
                        "loaded",
                        "initialized_for_inference",
                        "inferred",
                        "backtested",
                        "completed",
                    ],
                )
                self.assertLess(report["training"]["end"], report["inference"][0]["timestamp"])
                self.assertTrue(
                    report["backtest"]["fills"], "Sample must demonstrate actual execution"
                )
                orders = {o["id"]: o for o in report["backtest"]["orders"]}
                for fill in report["backtest"]["fills"]:
                    self.assertGreater(fill["timestamp"], orders[fill["order_id"]]["submitted_at"])
                    self.assertGreaterEqual(float(fill["cash_after"]), 0)
                    self.assertGreaterEqual(float(fill["position_after"]), 0)
                counts[m["kind"]] += 1
                source_hashes.add(digest((directory / "strategy.py").read_text()))
                model = read_json(self.path / directory.name / "model.json")["payload"]
                if m["kind"] != "rule":
                    algorithms.add(model["algorithm"])
                if m["kind"] == "rl":
                    self.assertGreater(model["updates"], 0)
                    self.assertTrue(any(v != 0 for row in model["q"] for v in row))
        self.assertEqual(counts, {"rule": 6, "supervised": 6, "rl": 6})
        self.assertEqual(len(source_hashes), 18)
        self.assertEqual(len(algorithms), 12)

    def test_replay_model_and_future_data_changes_cannot_affect_past(self):
        strategy = ROOT / "examples/sl_ridge"
        data = market()
        first = run(strategy, data, self.path / "first")
        self.assertEqual(first["status"], "success", first["error"])
        again = run(strategy, data, self.path / "again")
        self.assertEqual(first["result_sha256"], again["result_sha256"])
        split = int(len(data["rows"]) * 0.6)
        evaluation = {**data, "rows": data["rows"][split:]}
        replay = run(
            strategy,
            evaluation,
            self.path / "replay",
            mode="backtest",
            model_path=self.path / "first/model.json",
        )
        self.assertEqual(replay["backtest"], first["backtest"])
        self.assertEqual(replay["inference"], first["inference"])
        future = deepcopy(data)
        for row in future["rows"][-5:]:
            for field in ("open", "close", "high", "low"):
                row[field] += 10
        changed = run(strategy, future, self.path / "future")
        self.assertEqual(changed["status"], "success", changed["error"])
        self.assertEqual(first["inference"][:-5], changed["inference"][:-5])
        self.assertEqual(
            read_json(self.path / "first/model.json"), read_json(self.path / "future/model.json")
        )
        inference = run(
            strategy,
            evaluation,
            self.path / "infer",
            mode="infer",
            model_path=self.path / "first/model.json",
        )
        self.assertEqual(inference["status"], "success", inference["error"])
        self.assertEqual(len(inference["inference"]), 1)
        self.assertIsNone(inference["backtest"])

    def test_failures_always_create_machine_readable_evidence(self):
        cases = [
            ("missing_field", lambda d: d["rows"][0].pop("close"), "FIELD_MISSING"),
            ("frequency", lambda d: d.update(granularity="1m"), "GRANULARITY_MISMATCH"),
            ("symbol", lambda d: d["rows"][0].update(symbol="UNKNOWN"), "SYMBOL_UNMAPPED"),
        ]
        strategy = self.strategy()
        for name, mutate, code in cases:
            data = market()
            mutate(data)
            report = run(strategy, data, self.path / name)
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["error"]["code"], code)
            self.assertEqual(read_json(self.path / name / "errors.json"), [report["error"]])
        report = run(
            strategy,
            market(),
            self.path / "missing_model",
            mode="backtest",
            model_path=self.path / "absent.json",
        )
        self.assertEqual(report["error"]["code"], "MODEL_MISSING")

    def test_model_tampering_provenance_and_training_overlap(self):
        directory = self.strategy()
        report = run(directory, market(), self.path / "trained", mode="train")
        self.assertEqual(report["status"], "success")
        path = self.path / "trained/model.json"
        model = read_json(path)
        m, _, fp = load_strategy(directory)
        timestamp = model["training_end"] + 1
        self.error("DATA_LEAKAGE", load_model, path, m, fp, m["config"], timestamp - 1)
        self.error("MODEL_INCOMPATIBLE", load_model, path, m, "changed", m["config"], timestamp)
        changed = deepcopy(model)
        changed["payload"]["trained"] = False
        write_json(path, changed)
        self.error("MODEL_INVALID", load_model, path, m, fp, m["config"], timestamp)

    def test_training_illegal_actions_and_engine_failure_do_not_fallback(self):
        source = SOURCE.replace('return {"trained": True}', 'raise RuntimeError("training broke")')
        result = run(self.strategy(source), market(), self.path / "training")
        self.assertEqual(result["error"]["code"], "TRAINING_FAILED")
        self.assertFalse((self.path / "training/model.json").exists())
        source = SOURCE.replace(
            '"orders": []', '"orders": [{"type": "target", "symbol": "SIM", "quantity": -1}]'
        )
        result = run(self.strategy(source), market(), self.path / "illegal")
        self.assertEqual(result["error"]["code"], "ACTION_INVALID")

        class BrokenEngine(SpotEngine):
            def on_event(self, event):
                raise RuntimeError("adapter disconnected")

        result = run(self.strategy(), market(), self.path / "engine", engine_factory=BrokenEngine)
        self.assertEqual(result["error"]["code"], "BACKTEST_FAILED")
        self.assertEqual(result["error"]["stage"], "backtest")
        self.assertIsNone(result["backtest"])

    def test_no_trade_is_explained_and_previous_output_is_preserved(self):
        result = self.execute()
        self.assertEqual(result["status"], "success")
        self.assertTrue(result["backtest"]["no_trades"])
        self.assertEqual(Decimal(result["backtest"]["pnl"]), 0)
        self.error("OUTPUT_EXISTS", run, self.path / "strategy", market(), self.path / "out")

    def test_strategy_cannot_read_undeclared_market_fields(self):
        source = SOURCE.replace(
            '"diagnostics": {}', '"diagnostics": {"leak": history[-1]["volume"]}'
        )
        result = run(self.strategy(source), market(), self.path / "projection")
        self.assertEqual(result["error"]["code"], "INFERENCE_FAILED")
        self.assertIn("volume", result["error"]["message"])

    def test_empty_invalid_config_never_becomes_default(self):
        result = self.execute(config=[])
        self.assertEqual(result["error"]["code"], "SCHEMA_INVALID")

    def test_broken_adapter_snapshot_cannot_mask_original_failure(self):
        class BrokenAdapter(SpotEngine):
            def on_event(self, event):
                raise RuntimeError("original failure")

            def snapshot(self):
                raise ValueError("broken snapshot")

        result = self.execute(engine_factory=BrokenAdapter)
        self.assertEqual(result["error"]["code"], "BACKTEST_FAILED")
        self.assertEqual(result["error"]["message"], "original failure")
        self.assertEqual(result["error"]["details"]["snapshot_error"], "ValueError")

    def test_cumulative_strategy_output_is_bounded(self):
        source = SOURCE.replace('"diagnostics": {}', '"diagnostics": {"large": "x" * 1000000}')
        result = run(self.strategy(source), market(), self.path / "log-budget")
        self.assertEqual(result["error"]["code"], "RESOURCE_LIMIT")
        self.assertLessEqual(len(result["inference"]), 16)
