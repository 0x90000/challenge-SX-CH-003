"""Private JSON-lines worker, launched with python -I -S in an empty directory."""

import builtins
import json
import math
import sys
from pathlib import Path

# The script location is trusted; cwd, PYTHONPATH and user site-packages are excluded.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from psrc.codec import MAX_BYTES, decode, encode  # noqa: E402
from psrc.errors import ContractError  # noqa: E402
from psrc.learning import API  # noqa: E402
from psrc.sandbox import SAFE_BUILTINS, validate_source  # noqa: E402


def guard(event, args):
    if event in {
        "open",
        "os.system",
        "os.posix_spawn",
        "subprocess.Popen",
        "ctypes.dlopen",
    } or event.startswith(
        (
            "socket.",
            "os.exec",
            "os.spawn",
            "os.remove",
            "os.rename",
            "os.mkdir",
            "os.rmdir",
            "os.chdir",
            "os.fork",
        )
    ):
        raise ContractError("SANDBOX_VIOLATION", "Host operation denied", details={"event": event})


def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name != "math" or level:
        raise ContractError("SANDBOX_VIOLATION", "Import denied")
    return math


def main():
    if sys.platform != "win32":
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
        resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    sys.addaudithook(guard)
    namespace = {}
    budget = 2_000_000
    stage = "load"
    for raw in sys.stdin.buffer:
        try:
            request = decode(raw)
            stage = request["operation"]
            if stage == "load":
                tree = validate_source(request["source"])
                safe = {name: getattr(builtins, name) for name in SAFE_BUILTINS}
                safe["__import__"] = safe_import
                namespace = {"__builtins__": safe, "api": dict(API)}
                exec(compile(tree, "<strategy>", "exec"), namespace)
                budget = request["budget"]
                result = {
                    "loaded": True,
                    "language": "restricted-python-v1",
                    "os_memory_limit": sys.platform != "win32",
                }
            else:
                remaining = budget

                def trace(frame, event, arg):
                    nonlocal remaining
                    if event in ("call", "line"):
                        remaining -= 1
                        if remaining <= 0:
                            raise ContractError(
                                "RESOURCE_LIMIT", "Strategy instruction budget exhausted"
                            )
                    return trace

                sys.settrace(trace)
                try:
                    if stage == "initialize":
                        result = namespace["initialize"](request["config"])
                    elif stage == "train":
                        result = namespace["train"](request["rows"], request["config"])
                    elif stage == "infer":
                        result = namespace["infer"](
                            request["history"],
                            request["account"],
                            request["model"],
                            request["state"],
                        )
                    else:
                        raise ContractError("SCHEMA_INVALID", "Unknown worker operation")
                finally:
                    sys.settrace(None)
            output = encode({"result": result})
            if len(output) > MAX_BYTES:
                raise ContractError("RESOURCE_LIMIT", "Strategy response too large")
        except BaseException as exc:
            sys.settrace(None)
            if isinstance(exc, ContractError):
                error = exc.to_dict()
            else:
                code = {
                    "train": "TRAINING_FAILED",
                    "infer": "INFERENCE_FAILED",
                    "initialize": "INITIALIZATION_FAILED",
                }.get(stage, "STRATEGY_LOAD_FAILED")
                if isinstance(exc, MemoryError):
                    code = "RESOURCE_LIMIT"
                error = {
                    "code": code,
                    "message": str(exc)[:300],
                    "details": {"exception": type(exc).__name__},
                }
            output = json.dumps({"error": error}, ensure_ascii=True).encode()
        sys.stdout.buffer.write(output + b"\n")
        sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
