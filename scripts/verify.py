"""One command: stdlib tests, 18 strategies, fault evidence, benchmark, checksums."""

import argparse
import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from psrc.cli import suite  # noqa: E402
from psrc.codec import write_json  # noqa: E402
from scripts.benchmark import benchmark  # noqa: E402
from scripts.failure_cases import demonstrate  # noqa: E402
from scripts.package_submission import source_files  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output", type=Path, default=ROOT / "artifacts" / time.strftime("verify-%Y%m%d-%H%M%S")
    )
    parser.add_argument(
        "--coverage", action="store_true", help="Use pinned optional pytest/coverage tooling"
    )
    parser.add_argument("--benchmark-events", type=int, default=100_000)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        parser.error("Output already exists; choose a new evidence directory")
    output.mkdir(parents=True)
    env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    if args.coverage:
        command = [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "pytest_cov",
            "--cov=psrc",
            "--cov-branch",
            "--cov-report=term-missing",
            f"--cov-report=json:{output / 'coverage.json'}",
            f"--junitxml={output / 'junit.xml'}",
            "-q",
        ]
    else:
        command = [sys.executable, "-m", "unittest", "discover", "-v"]
    tested = subprocess.run(
        command, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    (output / "tests.txt").write_text(tested.stdout, encoding="utf-8")
    print(tested.stdout, flush=True)
    if tested.returncode:
        write_json(output / "verification.json", {"status": "failed", "stage": "tests"})
        return 1
    result = suite(output / "strategies")
    failures = demonstrate(output / "faults")
    measured = benchmark(ROOT, args.benchmark_events)
    write_json(output / "benchmark.json", measured)
    hashes = {name: hashlib.sha256(path.read_bytes()).hexdigest() for path, name in source_files()}
    write_json(output / "source-sha256.json", hashes)
    success = result["status"] == failures["status"] == "success"
    evidence = {
        "challenge": "SX-CH-003",
        "status": "success" if success else "failed",
        "tests": "tests.txt",
        "suite": "strategies/summary.json",
        "faults": "faults/failures.json",
        "benchmark": "benchmark.json",
        "sources": "source-sha256.json",
        "coverage": "coverage.json" if args.coverage else "not collected (stdlib execution)",
        "docker_executed": False,
        "note": "Local evidence only; final grade belongs to the challenge organizer",
    }
    write_json(output / "verification.json", evidence)
    print(f"Evidence: {output}\nStatus: {evidence['status']}")
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
