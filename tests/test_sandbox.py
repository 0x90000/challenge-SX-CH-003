import json
import os
import subprocess
import venv

from psrc.sandbox import Worker, validate_source

from .helpers import ROOT, SOURCE, Case


class SandboxTests(Case):
    def test_clean_venv_can_launch_worker_with_single_process_limit(self):
        directory = self.path / "venv"
        venv.EnvBuilder(with_pip=False).create(directory)
        python = directory / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        code = (
            "import sys, json; sys.path.insert(0, sys.argv[1]); "
            "from psrc.sandbox import Worker; from tests.helpers import SOURCE; "
            "worker = Worker(SOURCE); "
            "print(json.dumps(worker.call('train', rows=[], config={}))); worker.close()"
        )
        result = subprocess.run(
            [str(python), "-c", code, str(ROOT)],
            cwd=self.path,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"trained": True})

    def test_files_network_imports_introspection_and_code_execution_denied(self):
        attacks = [
            "import os",
            "import socket",
            "import pathlib",
            "from math import __dict__",
            'open("secret", "w")',
            'eval("1")',
            'exec("1")',
            "globals()",
            "().__class__.__mro__",
            'api["price"].__globals__',
            '"{0.__class__}".format(1)',
            'getattr(1, "real")',
        ]
        for attack in attacks:
            with self.subTest(attack=attack):
                source = SOURCE.replace('return {"trained": True}', attack + "\n    return {}")
                self.error("SANDBOX_VIOLATION", validate_source, source)
        for prefix in ("x = 1\n", "@print\n", "class Bad:\n    pass\n"):
            self.error("SANDBOX_VIOLATION", validate_source, prefix + SOURCE)

    def test_allowed_math_and_no_host_file_handles(self):
        source = "import math\n\n" + SOURCE.replace('{"trained": True}', '{"root": math.sqrt(9)}')
        with Worker(source) as worker:
            self.assertEqual(worker.call("initialize", config={}), {})
            self.assertEqual(worker.call("train", rows=[], config={}), {"root": 3})
            self.assertEqual(
                worker.call("infer", history=[], account={}, model={}, state={})["orders"], []
            )
        self.assertIsNotNone(worker.process.returncode)

    def test_instruction_budget_and_c_builtin_wall_timeout(self):
        source = SOURCE.replace('return {"trained": True}', "while True:\n        pass")
        with Worker(source, budget=1000) as worker:
            self.error("RESOURCE_LIMIT", worker.call, "train", rows=[], config={})
        source = SOURCE.replace(
            'return {"trained": True}', 'return {"value": sum(range(1000000000))}'
        )
        with Worker(source, timeout=0.5) as worker:
            self.error("SANDBOX_TIMEOUT", worker.call, "train", rows=[], config={})

    def test_os_memory_limit_contains_large_allocation(self):
        source = SOURCE.replace('return {"trained": True}', 'return {"value": [0] * 100000000}')
        with Worker(source) as worker:
            with self.assertRaises(Exception) as caught:
                worker.call("train", rows=[], config={})
            self.assertIn(caught.exception.code, {"RESOURCE_LIMIT", "SANDBOX_CRASH"})

    def test_training_and_inference_exceptions_keep_stage(self):
        source = SOURCE.replace(
            'return {"trained": True}', 'raise ValueError("bad training labels")'
        )
        with Worker(source) as worker:
            error = self.error("TRAINING_FAILED", worker.call, "train", rows=[], config={})
            self.assertEqual(error.stage, "train")
            self.assertIn("bad training labels", str(error))
        source = SOURCE.replace(
            'return {"orders": [], "state": state, "diagnostics": {}}',
            'raise RuntimeError("inference failed")',
        )
        with Worker(source) as worker:
            self.error(
                "INFERENCE_FAILED", worker.call, "infer", history=[], account={}, model={}, state={}
            )

    def test_invalid_source_and_non_json_result(self):
        self.error("STRATEGY_LOAD_FAILED", validate_source, "def broken(")
        self.error(
            "STRATEGY_LOAD_FAILED", validate_source, "def train(rows, config):\n    return {}"
        )
        source = SOURCE.replace('return {"trained": True}', 'return {"x": float("nan")}')
        with Worker(source) as worker:
            self.error("SCHEMA_INVALID", worker.call, "train", rows=[], config={})
