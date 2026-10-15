import importlib.util
import unittest

from psrc.codec import read_json
from psrc.runtime import run

from .helpers import ROOT, Case, market


@unittest.skipUnless(
    importlib.util.find_spec("jsonschema"), "Optional jsonschema dev dependency missing"
)
class SchemaTests(Case):
    def test_schemas_validate_real_inputs_models_and_success_failure_reports(self):
        from jsonschema import Draft202012Validator

        validators = {}
        for path in (ROOT / "schemas").glob("*.json"):
            schema = read_json(path)
            Draft202012Validator.check_schema(schema)
            validators[path.name.split(".")[0]] = Draft202012Validator(schema)
        for path in (ROOT / "examples").glob("*/manifest.json"):
            validators["manifest"].validate(read_json(path))
        for path in (ROOT / "data").glob("*.json"):
            validators["marketdata"].validate(read_json(path))
        strategy = self.strategy()
        success = run(strategy, market(), self.path / "success")
        validators["report"].validate(success)
        validators["model"].validate(read_json(self.path / "success/model.json"))
        invalid = market()
        invalid["rows"][0].pop("close")
        failure = run(strategy, invalid, self.path / "failure")
        validators["report"].validate(failure)
        validators["error"].validate(failure["error"])
