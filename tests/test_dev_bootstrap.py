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
    def test_sha256_file_hashes_exact_bytes(self) -> None:
        architecture = (
            SCRIPT.parents[1]
            / "docs"
            / "specs"
            / "AI-Engineer-Bench-Architecture-v0.1.md"
        )
        self.assertEqual(
            aieb_dev.sha256_file(architecture),
            "1acd59c8be999ae750d864415a5eb41bba441e19e0a499e6984c3b98261b1017",
        )

    def test_repository_contract_is_satisfied(self) -> None:
        self.assertEqual(aieb_dev.verify_repository(), [])


if __name__ == "__main__":
    unittest.main()
