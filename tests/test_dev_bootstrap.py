from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "dev.py"
SPEC = importlib.util.spec_from_file_location("aieb_dev", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
aieb_dev = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(aieb_dev)


class BootstrapChecksTest(unittest.TestCase):
    def test_repository_contract_is_satisfied(self) -> None:
        self.assertEqual(aieb_dev.verify_repository(), [])


if __name__ == "__main__":
    unittest.main()
