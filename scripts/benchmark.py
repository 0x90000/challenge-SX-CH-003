"""Measure validation and execution separately; no unmeasured speed claims."""

import platform
import time

from psrc.codec import read_json
from psrc.contract import validate_data
from psrc.data import synthetic
from psrc.engine import EngineConfig, SpotEngine


def benchmark(root, events=100_000):
    manifest = read_json(root / "examples/rule_sma/manifest.json")
    data = synthetic("1d", events)
    started = time.perf_counter()
    validate_data(data, manifest)
    validation = time.perf_counter() - started
    engine = SpotEngine(manifest, EngineConfig(initial_cash=1_000_000))
    started = time.perf_counter()
    for index, row in enumerate(data["rows"]):
        engine.on_event(row)
        if index % 10 == 0:
            quantity = 0 if engine.positions["SIM"] else 4
            engine.submit([{"type": "target", "symbol": "SIM", "quantity": quantity}], row)
    result = engine.finish()
    elapsed = time.perf_counter() - started
    return {
        "events": events,
        "fills": len(result["fills"]),
        "validation_seconds": validation,
        "engine_seconds": elapsed,
        "validation_events_per_second": events / validation,
        "engine_events_per_second": events / elapsed,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "scope": "trusted engine and validation only; excludes worker IPC and model training",
        "complexity": "O(N) for bounded outstanding orders; O(N) report storage",
    }
