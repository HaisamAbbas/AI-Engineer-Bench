from __future__ import annotations

import unittest

from pydantic import ValidationError
from scripts.validate_bugfinding_track import audit
from aieb_core.bugfinding_v2 import BugFinding, BugPatch, BugReleaseManifest, BugSubmission


class BugFindingContractsTest(unittest.TestCase):
    def test_finding_requires_reproducible_evidence_and_safe_location(self):
        with self.assertRaises(ValidationError):
            BugFinding(schema_version="aieb.bug-finding/v2", finding_id="x", location="../secret", symbol_or_line="1", reproduction_steps=("run",), observed_behavior="bad", expected_behavior="good", impact="high", severity="major", evidence=(), confidence=0.8)

    def test_modes_cannot_be_mixed(self):
        payload = {"schema_version": "aieb.bug-submission/v2", "repository": {}, "mode": "finding-only", "findings": []}
        with self.assertRaises(ValidationError):
            BugSubmission(**payload, patch={"patch_digest": "0" * 64, "changed_paths": ("src/a.py",)})

    def test_release_cannot_be_official_or_cross_cohort(self):
        self.assertFalse(audit()["official_release_eligible"])
        with self.assertRaises(ValidationError):
            BugReleaseManifest(schema_version="aieb.bug-release/v2", release_id="mvp2-bugfinding-release-x", cohort_id="mvp2-bugfinding-a", tasks=(), status="official")


if __name__ == "__main__":
    unittest.main()
