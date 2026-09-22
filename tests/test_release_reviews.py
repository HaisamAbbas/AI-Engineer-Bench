"""Pure validation regressions for V2-GAP-006 review provenance."""
from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
for source in (ROOT / "services/api/src", ROOT / "packages/aieb-core/src"):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from aieb_api import release_reviews  # noqa: E402
from aieb_api.errors import ApiError  # noqa: E402


class ReleaseReviewValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.campaign = SimpleNamespace(
            id=uuid.uuid4(), manifest_digest="a" * 64,
            cohort_digest="b" * 64, matrix_digest="c" * 64,
        )

    def test_campaign_digest_binds_all_frozen_identities(self) -> None:
        first = release_reviews.campaign_review_digest(self.campaign)
        self.campaign.matrix_digest = "d" * 64
        self.assertNotEqual(first, release_reviews.campaign_review_digest(self.campaign))

    def test_missing_independence_is_rejected_before_database_access(self) -> None:
        with self.assertRaises(ApiError):
            release_reviews.record_release_review(
                None, target_type="campaign_approval", target_id=self.campaign.id,
                reviewer_id=uuid.uuid4(), decision="approve", scope="campaign-approval",
                evidence_digest_value="a" * 64, independence_declaration=False,
                reason="reviewed",
            )

    def test_blank_reason_and_malformed_digest_are_rejected(self) -> None:
        for digest, reason in (("not-a-digest", "reviewed"), ("a" * 64, "  ")):
            with self.assertRaises(ApiError):
                release_reviews.record_release_review(
                    None, target_type="campaign_approval", target_id=self.campaign.id,
                    reviewer_id=uuid.uuid4(), decision="approve", scope="campaign-approval",
                    evidence_digest_value=digest, independence_declaration=True,
                    reason=reason,
                )


if __name__ == "__main__":
    unittest.main()
