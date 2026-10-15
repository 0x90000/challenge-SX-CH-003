import contextlib
import io
import json

from psrc.cli import main, suite
from psrc.codec import write_json

from .helpers import Case, market


class CLITests(Case):
    def test_cli_success_failure_and_exit_status(self):
        strategy = self.strategy()
        data = self.path / "input.json"
        write_json(data, market())
        args = [
            "train",
            "--strategy",
            str(strategy),
            "--data",
            str(data),
            "--output",
            str(self.path / "trained"),
        ]
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(args), 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "success")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(args), 1)
            args[-1] = str(self.path / "missing")
            args[0] = "backtest"
            self.assertEqual(main(args), 1)
            args[-1] = str(self.path / "badconfig")
            self.assertEqual(main(args + ["--config", str(self.path / "absent")]), 1)

    def test_suite_refuses_to_claim_18_strategy_coverage_for_empty_catalog(self):
        directory = self.path / "empty"
        directory.mkdir()
        with contextlib.redirect_stdout(io.StringIO()):
            result = suite(self.path / "report", examples=directory)
        self.assertEqual(result["status"], "failed")

    def test_malformed_suite_manifest_returns_structured_error(self):
        directory = self.path / "catalog/broken"
        write_json(directory / "manifest.json", {"id": "broken"})
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(
                ["suite", "--examples", str(directory.parent), "--output", str(self.path / "suite")]
            )
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue())["error"]["code"], "SCHEMA_INVALID")
