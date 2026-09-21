from __future__ import annotations

import unittest
from pathlib import Path

from scripts.run_harbor_admission import _validate_package


class HarborAdmissionRunnerTest(unittest.TestCase):
    def test_missing_harbor_package_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            _validate_package(Path(__file__).parents[1] / "suites" / "dev" / "ext.batch-alignment")

    def test_converted_rag_task_has_harbor_package_shape(self) -> None:
        _validate_package(Path(__file__).parents[1] / "suites" / "dev" / "rag.document-freshness")


if __name__ == "__main__":
    unittest.main()
