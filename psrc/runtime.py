"""Lifecycle coordinator. Untrusted code only receives JSON copies over a pipe."""

import hashlib
import platform
import time
from collections import defaultdict, deque
from dataclasses import asdict
from pathlib import Path

from . import __version__
from .codec import MAX_BYTES, digest, encode, read_json, write_json
from .compatibility import convert
from .contract import VERSION, keys, load_strategy, validate_data, validate_orders
from .engine import EngineConfig, SpotEngine
from .errors import ContractError, require
from .sandbox import Worker


def sdk_hash():
    return hashlib.sha256(Path(__file__).with_name("learning.py").read_bytes()).hexdigest()


def save_model(path, payload, manifest, fingerprint, training, config):
    require(
        isinstance(payload, dict) and bool(payload),
        "MODEL_INVALID",
        "Training must return a nonempty JSON model object",
    )
    model = {
        "contract_version": VERSION,
        "strategy_id": manifest["id"],
        "strategy_sha256": fingerprint,
        "sdk_sha256": sdk_hash(),
        "config_sha256": digest(config),
        "training_data_sha256": digest(training),
        "training_end": training[-1]["timestamp"],
        "training_rows": len(training),
        "payload": payload,
        "payload_sha256": digest(payload),
    }
    model["envelope_sha256"] = digest(model)
    write_json(path, model)
    return model


def load_model(path, manifest, fingerprint, config, first_timestamp):
    model = read_json(path, "MODEL_MISSING")
    keys(
        model,
        {
            "contract_version",
            "strategy_id",
            "strategy_sha256",
            "sdk_sha256",
            "config_sha256",
            "training_data_sha256",
            "training_end",
            "training_rows",
            "payload",
            "payload_sha256",
            "envelope_sha256",
        },
        code="MODEL_INVALID",
    )
    require(
        type(model["training_rows"]) is int
        and model["training_rows"] > 0
        and type(model["training_end"]) is int
        and model["training_end"] >= 0,
        "MODEL_INVALID",
        "Invalid training metadata",
    )
    unsigned = {k: v for k, v in model.items() if k != "envelope_sha256"}
    require(
        digest(unsigned) == model["envelope_sha256"]
        and digest(model["payload"]) == model["payload_sha256"],
        "MODEL_INVALID",
        "Model integrity check failed",
    )
    require(
        model["contract_version"] == VERSION
        and model["strategy_id"] == manifest["id"]
        and model["strategy_sha256"] == fingerprint
        and model["sdk_sha256"] == sdk_hash()
        and model["config_sha256"] == digest(config),
        "MODEL_INCOMPATIBLE",
        "Model provenance does not match strategy, SDK or configuration",
    )
    require(
        type(model["training_end"]) is int and model["training_end"] < first_timestamp,
        "DATA_LEAKAGE",
        "Training must finish strictly before inference data starts",
    )
    require(
        isinstance(model["payload"], dict) and bool(model["payload"]),
        "MODEL_INVALID",
        "Model payload must be a nonempty object",
    )
    return model


def _window(row, manifest):
    # Engine prices and undeclared future fields never leak into the strategy feature view.
    return {key: row[key] for key in ("timestamp", "symbol", *manifest["fields"])}


def run(
    strategy,
    data,
    output,
    *,
    mode="run",
    model_path=None,
    config=None,
    compatibility=None,
    train_fraction=0.6,
    timeout=5.0,
    engine_factory=SpotEngine,
):
    output = Path(output)
    require(
        not output.exists() or not any(output.iterdir()),
        "OUTPUT_EXISTS",
        "Use a new/empty output directory to preserve previous evidence",
    )
    output.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    report = {
        "contract_version": VERSION,
        "runtime_version": __version__,
        "status": "failed",
        "mode": mode,
        "environment": {"python": platform.python_version(), "platform": platform.system()},
        "strategy": None,
        "input": None,
        "training": None,
        "artifacts": {},
        "lifecycle": [],
        "compatibility": [],
        "inference": [],
        "backtest": None,
        "error": None,
        "sandbox": {
            "language": "restricted-python-v1",
            "process_isolation": True,
            "network": "denied",
            "filesystem": "denied",
            "wall_timeout_seconds": timeout,
            "instruction_budget_per_call": 2_000_000,
            "os_memory_limit_bytes": 256 * 1024 * 1024,
            "os_limit_backend": "windows-job" if platform.system() == "Windows" else "rlimit",
        },
    }
    stage = "validation"
    engine = None
    try:
        require(mode in {"run", "train", "infer", "backtest"}, "SCHEMA_INVALID", "Unknown mode")
        engine_config = EngineConfig.from_dict({} if config is None else config)
        report["engine_config"] = asdict(engine_config)
        manifest, source, fingerprint = load_strategy(strategy)
        report["strategy"] = manifest
        report["strategy_sha256"] = fingerprint
        raw = read_json(data) if isinstance(data, (str, Path)) else data
        report["input"] = {"raw_sha256": digest(raw)}
        converted = convert(raw, compatibility, report["compatibility"])
        rows = validate_data(converted, manifest)
        report["input"].update(
            sha256=digest(converted),
            granularity=converted["granularity"],
            provenance=converted["provenance"],
            rows=len(rows),
            fields=sorted(rows[0]),
            first_timestamp=rows[0]["timestamp"],
            last_timestamp=rows[-1]["timestamp"],
        )
        training, evaluation = rows, rows
        if mode == "run":
            require(
                type(train_fraction) in (int, float) and 0 < train_fraction < 1,
                "SCHEMA_INVALID",
                "train_fraction must be between 0 and 1",
            )
            split = int(len(rows) * train_fraction)
            require(0 < split < len(rows), "DATA_INVALID", "Not enough events to split")
            cutoff = rows[split]["timestamp"]
            training = [r for r in rows if r["timestamp"] < cutoff]
            evaluation = [r for r in rows if r["timestamp"] >= cutoff]
        strategy_config = manifest.get("config", {})
        if mode in {"run", "train"}:
            require(bool(training), "DATA_INVALID", "Empty training partition")
            stage = "initialize"
            with Worker(source, timeout=timeout) as worker:
                state = worker.call("initialize", config=strategy_config)
                require(
                    isinstance(state, dict), "SCHEMA_INVALID", "initialize must return state object"
                )
                report["lifecycle"].append("initialized_for_training")
                stage = "train"
                payload = worker.call(
                    "train", rows=[_window(r, manifest) for r in training], config=strategy_config
                )
                report["lifecycle"].append("trained")
            stage = "save"
            model_path = output / "model.json"
            model = save_model(
                model_path, payload, manifest, fingerprint, training, strategy_config
            )
            report["training"] = {
                "rows": len(training),
                "start": training[0]["timestamp"],
                "end": training[-1]["timestamp"],
                "data_sha256": digest(training),
            }
            report["artifacts"]["model"] = {"path": "model.json", "sha256": digest(model)}
            report["lifecycle"].append("saved")
        if mode != "train":
            stage = "load"
            require(
                model_path is not None, "MODEL_MISSING", "Inference/backtest requires a model file"
            )
            model = load_model(
                model_path, manifest, fingerprint, strategy_config, evaluation[0]["timestamp"]
            )
            report["lifecycle"].append("loaded")
            report["loaded_model_sha256"] = digest(model)
            report["training"] = report["training"] or {
                "rows": model["training_rows"],
                "end": model["training_end"],
                "data_sha256": model["training_data_sha256"],
            }
            stage = "backtest"
            engine = engine_factory(manifest, engine_config)
            history = defaultdict(lambda: deque(maxlen=manifest["lookback"]))
            seen = set()
            inference_bytes = 0
            with Worker(source, timeout=timeout) as worker:
                state = worker.call("initialize", config=strategy_config)
                require(
                    isinstance(state, dict), "SCHEMA_INVALID", "initialize must return state object"
                )
                report["lifecycle"].append("initialized_for_inference")
                for row in evaluation:
                    stage = "backtest"
                    engine.on_event(row)
                    symbol = row["symbol"]
                    history[symbol].append(_window(row, manifest))
                    if len(history[symbol]) < manifest["lookback"]:
                        continue
                    if mode == "infer" and row is not evaluation[-1]:
                        continue
                    stage = "infer"
                    response = worker.call(
                        "infer",
                        history=list(history[symbol]),
                        account=engine.snapshot(),
                        model=model["payload"],
                        state=state,
                    )
                    inference_bytes += len(encode(response))
                    require(
                        inference_bytes <= MAX_BYTES,
                        "RESOURCE_LIMIT",
                        "Cumulative inference output exceeds 16 MiB budget",
                    )
                    keys(response, {"orders", "state", "diagnostics"})
                    require(
                        isinstance(response["state"], dict)
                        and isinstance(response["diagnostics"], dict),
                        "SCHEMA_INVALID",
                        "Inference state and diagnostics must be objects",
                    )
                    validate_orders(response["orders"], manifest)
                    state = response["state"]
                    report["inference"].append(
                        {
                            "timestamp": row["timestamp"],
                            "symbol": symbol,
                            "orders": response["orders"],
                            "state_sha256": digest(state),
                            "diagnostics": response["diagnostics"],
                        }
                    )
                    seen.add(symbol)
                    if mode != "infer":
                        stage = "backtest"
                        engine.submit(response["orders"], row)
            require(bool(report["inference"]), "DATA_INVALID", "Evaluation never reached lookback")
            require(
                mode == "infer" or seen == set(manifest["symbols"]),
                "DATA_INVALID",
                "Some symbols have insufficient evaluation history",
            )
            report["lifecycle"].append("inferred")
            if mode != "infer":
                stage = "backtest"
                report["backtest"] = engine.finish()
                report["lifecycle"].append("backtested")
                report["backtest"]["no_trades"] = not bool(report["backtest"]["fills"])
        report["status"] = "success"
        report["lifecycle"].append("completed")
    except Exception as exc:
        if isinstance(exc, ContractError):
            error = exc.to_dict()
            error["stage"] = stage
        else:
            code = {
                "train": "TRAINING_FAILED",
                "backtest": "BACKTEST_FAILED",
                "infer": "INFERENCE_FAILED",
                "save": "IO_FAILED",
            }.get(stage, "RUNTIME_FAILED")
            error = ContractError(
                code, str(exc)[:300], stage=stage, details={"exception": type(exc).__name__}
            ).to_dict()
        report["error"] = error
        report["lifecycle"].append("failed")
        if engine is not None:
            try:
                report["partial_account"] = engine.snapshot()
            except Exception as snapshot_error:
                error["details"]["snapshot_error"] = type(snapshot_error).__name__
    report["elapsed_seconds"] = time.perf_counter() - started
    report["result_sha256"] = digest(
        {
            "status": report["status"],
            "strategy": report["strategy"],
            "input": report["input"],
            "training": report["training"],
            "inference": report["inference"],
            "backtest": report["backtest"],
            "error": report["error"],
        }
    )
    write_json(output / "report.json", report)
    write_json(output / "errors.json", [] if report["error"] is None else [report["error"]])
    write_json(output / "inference.json", report["inference"])
    return report
