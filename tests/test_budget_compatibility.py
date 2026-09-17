"""v1 digest compatibility and v2 complete reservation estimates."""
import unittest
from dataclasses import replace

from pydantic import ValidationError
from aieb_core.canonical import content_hash
from aieb_core.models import BudgetProfile, BudgetProfileV2, ResolvedCampaign
from aieb_core.planner import freeze_campaign
from aieb_api.budgets import reserved_amount_from_resolved
from tests.test_core_contracts import registry


class BudgetCompatibilityTests(unittest.TestCase):
    def test_v1_roundtrip_has_no_new_field_or_digest(self):
        source, draft = registry()
        budget = next(iter(source.budgets.values()))
        raw = budget.model_dump(mode="json")
        self.assertNotIn("environment_upper_bound_usd", raw)
        self.assertEqual(BudgetProfile.model_validate(raw).digest(), content_hash(raw))
        frozen = freeze_campaign(draft, source)
        self.assertEqual(ResolvedCampaign.model_validate(frozen.model_dump(mode="json")).digest(), frozen.digest())
        self.assertIsNone(reserved_amount_from_resolved(frozen.model_dump(mode="json")))

    def test_v2_roundtrip_and_environment_multiplies_with_trials_and_replacements(self):
        source, draft = registry()
        raw = next(iter(source.budgets.values())).model_dump(mode="json")
        raw.update(schema_version="aieb.budget/v2", environment_upper_bound_usd="2")
        for role in raw["per_role_budget_usd"]:
            role["limit_usd"] = "1"
        budget = BudgetProfileV2.model_validate(raw)
        source = replace(source, budgets={budget.id: budget})
        frozen = freeze_campaign(draft, source)
        resolved = frozen.model_dump(mode="json")
        self.assertEqual(ResolvedCampaign.model_validate(resolved).digest(), frozen.digest())
        self.assertEqual(reserved_amount_from_resolved(resolved), str(6 * len(frozen.trials) * 3))
        del resolved["budget"]["environment_upper_bound_usd"]
        self.assertIsNone(reserved_amount_from_resolved(resolved))
        with self.assertRaises(ValidationError):
            BudgetProfileV2.model_validate(resolved["budget"])

    def test_missing_role_bound_is_unknown_not_zero(self):
        source, draft = registry()
        raw = freeze_campaign(draft, source).model_dump(mode="json")
        raw["budget"]["environment_upper_bound_usd"] = "0"
        self.assertIsNone(reserved_amount_from_resolved(raw))
