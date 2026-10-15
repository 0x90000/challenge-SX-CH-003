"""Commands emit JSON and return nonzero on any unsuccessful run."""

import argparse
import json
from pathlib import Path

from .codec import read_json, write_json
from .contract import validate_manifest
from .errors import ContractError
from .runtime import run

ROOT = Path(__file__).resolve().parent.parent


def suite(output, examples=None, data=None):
    examples = Path(examples or ROOT / "examples")
    data = Path(data or ROOT / "data")
    results = []
    for directory in sorted(examples.iterdir()):
        if not directory.is_dir() or not (directory / "manifest.json").exists():
            continue
        manifest = validate_manifest(read_json(directory / "manifest.json"))
        report = run(
            directory, data / f"{manifest['granularity']}.json", Path(output) / directory.name
        )
        results.append(
            {
                "strategy": manifest["id"],
                "kind": manifest["kind"],
                "status": report["status"],
                "error": report["error"],
                "result_sha256": report["result_sha256"],
                "fills": len((report["backtest"] or {}).get("fills", [])),
                "elapsed_seconds": report["elapsed_seconds"],
            }
        )
        print(json.dumps(results[-1]), flush=True)
    counts = {
        kind: sum(r["kind"] == kind and r["status"] == "success" for r in results)
        for kind in ("rule", "supervised", "rl")
    }
    summary = {
        "challenge": "SX-CH-003",
        "results": results,
        "successful_by_kind": counts,
        "status": "success"
        if all(v >= 6 for v in counts.values()) and all(r["status"] == "success" for r in results)
        else "failed",
    }
    write_json(Path(output) / "summary.json", summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description="Paper Strategy Runtime Contract v1")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "train", "infer", "backtest"):
        command = commands.add_parser(name)
        command.add_argument("--strategy", required=True, type=Path)
        command.add_argument("--data", required=True, type=Path)
        command.add_argument("--output", required=True, type=Path)
        command.add_argument("--model", type=Path)
        command.add_argument("--config", type=Path)
        command.add_argument("--compatibility", type=Path)
        command.add_argument("--train-fraction", type=float, default=0.6)
        command.add_argument("--timeout", type=float, default=5.0)
    command = commands.add_parser("suite")
    command.add_argument("--output", type=Path, required=True)
    command.add_argument("--examples", type=Path)
    command.add_argument("--data", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "suite":
            result = suite(args.output, args.examples, args.data)
        else:
            report = run(
                args.strategy,
                args.data,
                args.output,
                mode=args.command,
                model_path=args.model,
                config=read_json(args.config) if args.config else None,
                compatibility=read_json(args.compatibility) if args.compatibility else None,
                train_fraction=args.train_fraction,
                timeout=args.timeout,
            )
            result = {
                "status": report["status"],
                "error": report["error"],
                "report": str(args.output / "report.json"),
            }
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "success" else 1
    except ContractError as exc:
        print(json.dumps({"status": "failed", "error": exc.to_dict()}, ensure_ascii=False))
        return 1
    except OSError as exc:
        print(json.dumps({"status": "failed", "error": {"code": "IO_FAILED", "message": str(exc)}}))
        return 1
