import copy
import tempfile
import time
import unittest
from pathlib import Path

from psrc.codec import read_json, write_json
from psrc.data import synthetic
from psrc.errors import ContractError

ROOT = Path(__file__).resolve().parents[1]
SOURCE = """def initialize(config):
    return {}

def train(rows, config):
    return {"trained": True}

def infer(history, account, model, state):
    return {"orders": [], "state": state, "diagnostics": {}}
"""


def manifest():
    return read_json(ROOT / "examples/rule_sma/manifest.json")


def market():
    return synthetic("1d", count=80)


class Case(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.cleanup_temp)
        self.path = Path(self.temp.name)

    def cleanup_temp(self):
        # Windows can retain the exited venv launcher's image mapping briefly.
        for attempt in range(20):
            try:
                self.temp.cleanup()
                return
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.1)

    def error(self, code, function, *args, **kwargs):
        with self.assertRaises(ContractError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def strategy(self, source=SOURCE, changes=None):
        directory = self.path / "strategy"
        directory.mkdir(exist_ok=True)
        m = copy.deepcopy(manifest())
        m.update(changes or {})
        write_json(directory / "manifest.json", m)
        (directory / "strategy.py").write_text(source, encoding="utf-8")
        return directory
