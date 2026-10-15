"""Restricted language and isolated persistent worker transport."""

import ast
import os
import queue
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from .codec import MAX_BYTES, decode, encode
from .errors import ContractError, require
from .limits import windows_job

SAFE_ATTRS = {
    "get",
    "items",
    "keys",
    "values",
    "append",
    "extend",
    "pop",
    "copy",
    "count",
    "index",
    "sqrt",
    "log",
    "exp",
    "tanh",
    "isfinite",
    "pi",
    "floor",
    "ceil",
    "sin",
    "cos",
    "fabs",
    "fsum",
}
BLOCKED_NODES = (
    ast.ClassDef,
    ast.With,
    ast.AsyncWith,
    ast.AsyncFunctionDef,
    ast.Await,
    ast.Yield,
    ast.YieldFrom,
    ast.Global,
    ast.Nonlocal,
    ast.Delete,
)
SAFE_BUILTINS = (
    "abs",
    "all",
    "any",
    "bool",
    "dict",
    "enumerate",
    "float",
    "int",
    "len",
    "list",
    "max",
    "min",
    "range",
    "reversed",
    "round",
    "set",
    "sorted",
    "str",
    "sum",
    "tuple",
    "zip",
    "ValueError",
    "RuntimeError",
)


def validate_source(source):
    require(
        isinstance(source, str) and len(source) <= 128_000,
        "SANDBOX_VIOLATION",
        "Invalid strategy source",
    )
    try:
        tree = ast.parse(source)
    except (SyntaxError, RecursionError) as exc:
        raise ContractError("STRATEGY_LOAD_FAILED", "Invalid Python source") from exc
    nodes = list(ast.walk(tree))
    require(len(nodes) <= 20_000, "RESOURCE_LIMIT", "Strategy AST exceeds node limit")
    for node in nodes:
        require(
            not isinstance(node, BLOCKED_NODES),
            "SANDBOX_VIOLATION",
            "Python construct is outside the restricted strategy language",
            construct=type(node).__name__,
        )
        if isinstance(node, (ast.Name, ast.arg)):
            name = node.id if isinstance(node, ast.Name) else node.arg
            require(not name.startswith("_"), "SANDBOX_VIOLATION", "Private names forbidden")
            require(
                name
                not in {
                    "open",
                    "eval",
                    "exec",
                    "compile",
                    "getattr",
                    "setattr",
                    "globals",
                    "locals",
                    "vars",
                    "input",
                    "breakpoint",
                    "help",
                },
                "SANDBOX_VIOLATION",
                "Dangerous builtin forbidden",
                name=name,
            )
        if isinstance(node, ast.Attribute):
            require(
                node.attr in SAFE_ATTRS,
                "SANDBOX_VIOLATION",
                "Attribute is not allowed",
                attribute=node.attr,
            )
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = (
                [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module]
            )
            require(
                modules == ["math"] and not getattr(node, "level", 0),
                "SANDBOX_VIOLATION",
                "Only the math module may be imported",
            )
            if isinstance(node, ast.ImportFrom):
                require(
                    all(a.name in SAFE_ATTRS for a in node.names),
                    "SANDBOX_VIOLATION",
                    "Imported math name is not allowed",
                )
            require(
                all(not (a.asname or a.name).startswith("_") for a in node.names),
                "SANDBOX_VIOLATION",
                "Private import alias forbidden",
            )
    # Module-level statements execute too; only definitions, imports and constants are allowed.
    for node in tree.body:
        require(
            isinstance(node, (ast.FunctionDef, ast.Import, ast.ImportFrom))
            or isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant),
            "SANDBOX_VIOLATION",
            "Top-level executable statements forbidden",
        )
        if isinstance(node, ast.FunctionDef):
            require(
                not node.decorator_list
                and not node.args.defaults
                and not any(node.args.kw_defaults)
                and node.returns is None
                and all(a.annotation is None for a in node.args.args + node.args.kwonlyargs),
                "SANDBOX_VIOLATION",
                "Decorators, defaults and annotations forbidden",
            )
    functions = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    require(
        {"initialize", "train", "infer"} <= functions,
        "STRATEGY_LOAD_FAILED",
        "Strategy must define initialize, train and infer",
    )
    return tree


class Worker:
    def __init__(self, source, *, timeout=5.0, budget=2_000_000):
        validate_source(source)
        number_ok = type(timeout) in (float, int) and 0 < timeout <= 60
        require(number_ok, "SCHEMA_INVALID", "Worker timeout must be in (0,60]")
        require(
            type(budget) is int and 100 <= budget <= 20_000_000,
            "SCHEMA_INVALID",
            "Invalid execution budget",
        )
        self.timeout = timeout
        self.temp = tempfile.TemporaryDirectory(prefix="psrc-worker-")
        env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR") if key in os.environ}
        self.process = subprocess.Popen(
            # Windows venv python.exe is a launcher; use the base interpreter so the
            # one-process Job limit does not block its hidden child process.
            [sys._base_executable, "-I", "-S", str(Path(__file__).with_name("worker.py"))],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=self.temp.name,
            env=env,
        )
        self.release_job = None
        try:
            self.release_job = windows_job(self.process)
        except OSError as exc:
            self.process.kill()
            self.process.wait()
            self.process.stdin.close()
            self.process.stdout.close()
            self.temp.cleanup()
            raise ContractError(
                "SANDBOX_UNAVAILABLE", "Cannot establish OS resource limits"
            ) from exc
        self.responses = queue.Queue(maxsize=1)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        try:
            self.call("load", source=source, budget=budget)
        except BaseException:
            self.close()
            raise

    def _read(self):
        try:
            while True:
                raw = self.process.stdout.readline(MAX_BYTES + 1)
                self.responses.put(raw)
                if not raw or len(raw) > MAX_BYTES:
                    break
        except (OSError, ValueError):
            self.responses.put(b"")

    def call(self, operation, **payload):
        raw = encode({"operation": operation, **payload}) + b"\n"
        require(len(raw) <= MAX_BYTES, "RESOURCE_LIMIT", "Worker request too large")
        try:
            self.process.stdin.write(raw)
            self.process.stdin.flush()
            response = self.responses.get(timeout=self.timeout)
        except queue.Empty as exc:
            self.process.kill()
            raise ContractError(
                "SANDBOX_TIMEOUT", "Strategy exceeded wall time", stage=operation
            ) from exc
        except (OSError, ValueError) as exc:
            raise ContractError(
                "SANDBOX_CRASH", "Worker transport failed", stage=operation
            ) from exc
        if not response:
            raise ContractError(
                "SANDBOX_CRASH", "Worker exited without a response", stage=operation
            )
        result = decode(response)
        if "error" in result:
            error = result["error"]
            raise ContractError(
                error["code"], error["message"], stage=operation, details=error.get("details")
            )
        return result["result"]

    def close(self):
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=5)
        self.reader.join(timeout=1)
        for stream in (self.process.stdin, self.process.stdout):
            try:
                stream.close()
            except OSError:
                # Windows may flush a broken pipe during close; preserve the original failure.
                pass
        if self.release_job:
            self.release_job()
            self.release_job = None
        self.temp.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
