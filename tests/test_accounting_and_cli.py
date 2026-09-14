from __future__ import annotations

import contextlib
import io
import json
import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from aieb_core.models import BudgetRole
from aieb_runner.accounting import BudgetEnforcement, UsageLedger, UsageReceipt, reserve_cost
from aieb_cli.main import EXIT_INVALID, EXIT_UNSOLVED, main


ROOT = Path(__file__).resolve().parents[1]


class AccountingTests(unittest.TestCase):
    def test_duplicate_lost_response_and_physical_retry_are_distinct(self) -> None:
        ledger = UsageLedger()
        self.assertTrue(ledger.record(UsageReceipt("a", BudgetRole.ENGINEER, "r1", 0, "adapter", cost_usd="1.00")))
        self.assertFalse(ledger.record(UsageReceipt("a", BudgetRole.ENGINEER, "r1", 0, "broker", input_tokens=4, output_tokens=2, cost_usd="1.00")))
        self.assertTrue(ledger.record(UsageReceipt("a", BudgetRole.ENGINEER, "r1", 1, "broker", cost_usd="2.00")))
        ledger.lost_response(attempt_id="a", role=BudgetRole.DEV_APPLICATION, request_id="lost", physical_attempt=0)
        self.assertEqual(len(ledger.receipts()), 3)
        engineer = ledger.summary(BudgetRole.ENGINEER)
        self.assertEqual(engineer.cost_usd, "3.00")
        self.assertIsNone(engineer.input_tokens)  # retry usage is unavailable, never inferred as zero
        unknown = ledger.summary(BudgetRole.DEV_APPLICATION)
        self.assertIsNone(unknown.cost_usd)
        self.assertTrue(unknown.billing_uncertain)

    def test_hard_cost_without_provider_reservation_is_not_claimed(self) -> None:
        soft = reserve_cost(hard_cost_requested=True, provider_supports_reservation=False, max_cost_usd="5")
        self.assertEqual(soft.enforcement, BudgetEnforcement.ESTIMATED_TIME_LIMITED)
        hard = reserve_cost(hard_cost_requested=True, provider_supports_reservation=True, max_cost_usd="5")
        self.assertEqual(hard.enforcement, BudgetEnforcement.HARD)
        self.assertEqual(hard.reserved_usd, "5")


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.campaign_id = f"cli-rag01-{uuid4().hex[:8]}"
        self.dir = ROOT / ".cache" / "eng008-009-tests" / self.campaign_id
        self.dir.mkdir(parents=True)
        self.campaign = self.dir / "campaign.json"
        self.campaign.write_text(json.dumps({"schema_version": "aieb.local-campaign/v1", "id": self.campaign_id, "task_dir": "suites/dev/rag.document-freshness", "candidate": "reference"}), encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)
        shutil.rmtree(ROOT / ".aieb" / "runs" / self.campaign_id, ignore_errors=True)

    def invoke(self, *args: str) -> tuple[int, dict[str, object]]:
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = main(["--json", "--no-color", *args])
        return code, json.loads(stream.getvalue())

    def test_local_cli_full_vertical_and_html_report(self) -> None:
        code, result = self.invoke("task", "validate", "suites/dev/rag.document-freshness")
        self.assertEqual(code, 0)
        self.assertEqual(result["task_id"], "rag.document-freshness")
        code, planned = self.invoke("plan", "--campaign", str(self.campaign))
        self.assertEqual(code, 0)
        self.assertEqual(planned["campaign_id"], self.campaign_id)
        code, completed = self.invoke("run", "--campaign", str(self.campaign))
        self.assertEqual(code, 0)  # a failed task would also be data, not a crash
        self.assertEqual(completed["verdict"], "pass")
        code, inspected = self.invoke("inspect", "--trial", self.campaign_id)
        self.assertEqual(code, 0)
        self.assertEqual(inspected["verdict"], "pass")
        code, report = self.invoke("report", "--campaign", self.campaign_id, "--format", "html")
        self.assertEqual(code, 0)
        self.assertTrue((ROOT / report["report"]).is_file())

    def test_unsolved_lock_and_invalid_resume_are_safe(self) -> None:
        self.campaign.write_text(json.dumps({"schema_version": "aieb.local-campaign/v1", "id": self.campaign_id, "task_dir": "suites/dev/rag.document-freshness", "candidate": "baseline"}), encoding="utf-8")
        self.assertEqual(self.invoke("run", "--campaign", str(self.campaign), "--fail-on-unsolved")[0], EXIT_UNSOLVED)
        _, report = self.invoke("report", "--campaign", self.campaign_id)
        self.assertIn("unavailable", (ROOT / report["report"]).read_text(encoding="utf-8"))
        state = ROOT / ".aieb" / "runs" / self.campaign_id
        (state / "controller.lock").write_text("other", encoding="utf-8")
        self.assertEqual(self.invoke("resume", self.campaign_id, "--campaign", str(self.campaign))[0], EXIT_INVALID)
        (state / "controller.lock").unlink()
        manifest = state / "frozen-manifest.json"
        value = json.loads(manifest.read_text(encoding="utf-8")); value["campaign_digest"] = "bad"; manifest.write_text(json.dumps(value), encoding="utf-8")
        self.assertEqual(self.invoke("resume", self.campaign_id, "--campaign", str(self.campaign))[0], EXIT_INVALID)
