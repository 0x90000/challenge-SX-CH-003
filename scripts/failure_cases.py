"""Executable evidence for every mandatory structured failure scenario."""

import tempfile
from pathlib import Path

from psrc.codec import read_json, write_json
from psrc.data import synthetic
from psrc.engine import SpotEngine
from psrc.runtime import run

ROOT = Path(__file__).resolve().parents[1]


def demonstrate(output):
    output = Path(output)
    results = []
    for name, mutation, expected in (
        ("field_missing", lambda d: d["rows"][0].pop("close"), "FIELD_MISSING"),
        ("granularity", lambda d: d.update(granularity="1m"), "GRANULARITY_MISMATCH"),
        ("symbol", lambda d: d["rows"][0].update(symbol="WRONG"), "SYMBOL_UNMAPPED"),
    ):
        data = synthetic("1d", 80)
        mutation(data)
        report = run(ROOT / "examples/rule_sma", data, output / name)
        results.append({"case": name, "expected": expected, "actual": report["error"]})
    data = synthetic("1d", 80)
    report = run(
        ROOT / "examples/rule_sma",
        data,
        output / "model_missing",
        mode="backtest",
        model_path=output / "nonexistent-model.json",
    )
    results.append(
        {"case": "model_missing", "expected": "MODEL_MISSING", "actual": report["error"]}
    )
    source = (ROOT / "examples/rule_sma/strategy.py").read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="psrc-faults-") as temp:
        strategy = Path(temp)
        write_json(strategy / "manifest.json", read_json(ROOT / "examples/rule_sma/manifest.json"))
        mutations = {
            "training": (
                source.replace(
                    'return {"kind": "rule", "fitted": False, "observations": len(rows)}',
                    'raise ValueError("injected training failure")',
                ),
                "TRAINING_FAILED",
            ),
            "illegal_order": (source.replace("8 if signal else 0", "-1"), "ACTION_INVALID"),
            "sandbox_import": ("import socket\n" + source, "SANDBOX_VIOLATION"),
        }
        for name, (changed, expected) in mutations.items():
            (strategy / "strategy.py").write_text(changed, encoding="utf-8")
            report = run(strategy, data, output / name)
            results.append({"case": name, "expected": expected, "actual": report["error"]})

    class BrokenEngine(SpotEngine):
        def on_event(self, event):
            raise RuntimeError("injected adapter failure")

    report = run(ROOT / "examples/rule_sma", data, output / "backtest", engine_factory=BrokenEngine)
    results.append({"case": "backtest", "expected": "BACKTEST_FAILED", "actual": report["error"]})
    for enabled in (False, True):
        renamed = synthetic("1d", 80)
        for row in renamed["rows"]:
            row["closing"] = row.pop("close")
        policy = {
            "enabled": enabled,
            "field_aliases": {"closing": "close"},
            "symbol_map": {},
            "reason": "Explicit fixture vendor field mapping",
        }
        report = run(
            ROOT / "examples/rule_sma",
            renamed,
            output / f"compatibility-{enabled}",
            compatibility=policy,
        )
        results.append(
            {
                "case": f"compatibility-{enabled}",
                "expected": None if enabled else "FIELD_MISSING",
                "actual": report["error"],
                "audit_entries": len(report["compatibility"]),
            }
        )
    success = all((r["actual"] or {}).get("code") == r["expected"] for r in results)
    result = {"status": "success" if success else "failed", "cases": results}
    write_json(output / "failures.json", result)
    return result
