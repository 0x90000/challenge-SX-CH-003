from psrc.codec import MAX_BYTES, decode
from psrc.contract import number
from psrc.engine import EngineConfig, SpotEngine
from psrc.learning import features, policy, predict, reinforcement, supervised
from psrc.sandbox import Worker
from psrc.worker import guard, safe_import

from .helpers import Case, SOURCE, manifest, market
from .test_engine import bar


class BoundaryTests(Case):
    def test_audit_guard_and_import_guard_deny_host_capabilities(self):
        for event in (
            "open",
            "socket.connect",
            "subprocess.Popen",
            "ctypes.dlopen",
            "os.fork",
            "os.chdir",
        ):
            self.error("SANDBOX_VIOLATION", guard, event, ())
        guard("unrelated.audit.event", ())
        self.error("SANDBOX_VIOLATION", safe_import, "os")
        self.assertEqual(safe_import("math").sqrt(4), 2)

    def test_numeric_overflow_and_oversized_input_are_structured(self):
        self.error("SCHEMA_INVALID", number, 10**1000)
        self.error("RESOURCE_LIMIT", decode, b" " * (MAX_BYTES + 1))

    def test_distinct_feature_sources_and_policy_inventory_validation(self):
        rows = market()["rows"]
        names = ["return", "momentum", "range", "volume", "imbalance", "depth", "spread", "flow"]
        self.assertEqual(len(features(rows[:8], names)), 8)
        model = reinforcement(rows, "q_learning", "momentum", 7)
        result = policy(rows[-8:], {"positions": {"SIM": 0}}, model)
        self.assertIn(result, [0, 4, 8])
        with self.assertRaises(ValueError):
            policy(rows[-8:], {"positions": {"SIM": 3}}, model)
        for function, args in [
            (supervised, (rows, "unknown", ["return"])),
            (reinforcement, (rows, "unknown", "return", 7)),
            (predict, (rows, {"algorithm": "unknown", "features": ["return"]})),
        ]:
            with self.assertRaises(ValueError):
                function(*args)

    def test_other_symbol_event_does_not_fill_pending_order(self):
        m = manifest()
        m["symbols"] = ["SIM", "SECOND"]
        engine = SpotEngine(m, EngineConfig())
        engine.on_event(bar(1))
        engine.submit([{"type": "target", "symbol": "SIM", "quantity": 4}], bar(1))
        engine.on_event(bar(2, symbol="SECOND"))
        self.assertEqual(engine.fills, [])
        engine.on_event(bar(3))
        self.assertEqual(len(engine.fills), 1)

    def test_worker_crash_is_not_silently_restarted(self):
        with Worker(SOURCE) as worker:
            worker.process.kill()
            worker.process.wait()
            with self.assertRaises(Exception) as caught:
                worker.call("initialize", config={})
            self.assertEqual(caught.exception.code, "SANDBOX_CRASH")
