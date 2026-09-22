from __future__ import annotations

import unittest
import os
from unittest.mock import patch

from pydantic import ValidationError

from aieb_core.contracts_v2 import ModelExecutionAuthorization
from scripts.audit_model_track_authorization import audit


class ModelTrackAuthorizationTest(unittest.TestCase):
    def test_readiness_is_fail_closed_without_paid_authorization(self) -> None:
        report = audit()
        self.assertTrue(report["fixed_contract_verified"])
        self.assertFalse(report["ready_for_real_execution"])
        self.assertFalse(report["credential_present"])
        self.assertFalse(report["official_release_eligible"])
        self.assertTrue(any("authorization" in error for error in report["errors"]))

    def test_authorization_requires_positive_cap_and_unique_models(self) -> None:
        common = {
            "schema_version": "aieb.model-execution-authorization/v1",
            "authorization_id": "auth-1",
            "cohort_id": "mvp2-model-development-v1",
            "provider_id": "provider",
            "requested_models": ("model-a",),
            "spend_cap_usd": "10.00",
            "credential_env_var": "AIEB_MODEL_TRACK_API_KEY",
            "approved_by": "operator",
            "approved_at": "2026-09-22T00:00:00Z",
            "expires_at": "2026-10-01T00:00:00Z",
            "purpose": "development pilot",
        }
        record = ModelExecutionAuthorization(**common)
        self.assertEqual(record.spend_cap_usd, "10")
        with self.assertRaises(ValidationError):
            ModelExecutionAuthorization(**{**common, "spend_cap_usd": "0"})
        with self.assertRaises(ValidationError):
            ModelExecutionAuthorization(**{**common, "requested_models": ("model-a", "model-a")})

    def test_readiness_report_never_contains_credential_value(self) -> None:
        sentinel = "provider-secret-must-not-appear"
        with patch.dict(os.environ, {"AIEB_MODEL_TRACK_API_KEY": sentinel}):
            report = audit()
        self.assertNotIn(sentinel, str(report))
        self.assertTrue(report["credential_present"])
        self.assertFalse(report["ready_for_real_execution"])


if __name__ == "__main__":
    unittest.main()
