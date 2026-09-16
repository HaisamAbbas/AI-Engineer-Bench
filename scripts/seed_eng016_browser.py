"""Seed one disposable published run for the real browser integration check.

Only writes to AIEB_DATABASE_URL; use a disposable PostgreSQL database.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
for source in (ROOT / "services/api/src", ROOT / "packages/aieb-core/src", ROOT / "packages/aieb-runner/src"):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from aieb_core.canonical import content_hash
from aieb_core.models import CandidateManifest
from aieb_api import db, models
from aieb_api.snapshots import snapshot_digest
from aieb_runner.artifacts import CandidateDiff, StoredCandidate
from aieb_api.worker.runner_bridge import _serialize_stored_candidate


def main() -> None:
    db.configure()
    task_id = "rag.document-freshness"
    task_version = "0.1.0"
    entrant_id = "browser-agent"
    protocol_id = "browser-methodology-v1"
    scoring_digest = "9" * 64
    task_manifest = {
        "schema_version": "aieb.task/v1", "id": task_id, "version": task_version,
        "family_id": "knowledge-service-a", "category": "rag", "activity": "repair",
        "source": {"repository_digest": "1" * 64, "commit": "browser-e2e-fixture", "license": "Apache-2.0", "provenance_digest": "2" * 64},
        "environment": {"official_image": "registry.example/aieb@sha256:" + "3" * 64, "engineer_cpu": 1, "engineer_memory_mb": 512, "service_topology_digest": "4" * 64, "egress_policy": "none"},
        "application": {"dependency_mode": "fixture", "entrypoint": ["python", "-m", "knowledge_service.server"], "contract_digest": "5" * 64, "model_profile_id": "deterministic-rag-fixture-v1"},
        "submission": {"include": ["knowledge_service/**"], "protected": ["dev_tests/**"], "max_artifact_bytes": 52428800},
        "requirements": [{"id": "api-ready", "severity": "mandatory", "description": "Health returns ready status."}],
        "evaluator": {"evaluator_digest": "6" * 64, "development_fixture": "rag01-dev-v1", "official_fixture_ref": "maintainer-only:rag01-v1"},
        "profile_compatibility": ["cpu-fixture-standard-v1"],
    }
    entrant_manifest = {
        "schema_version": "aieb.entrant/v1", "id": entrant_id, "track": "agents",
        "agent_implementation": "browser fixture", "agent_version": "1.0.0",
        "engineer_model": {"provider_class": "fixture", "requested_model": "deterministic", "reported_model": "deterministic", "settings_digest": "a" * 64},
        "prompt_digest": "b" * 64, "tools_digest": "c" * 64,
        "capabilities": ["cpu-fixture-standard-v1"], "credential_ref_type": "broker",
    }
    protocol_manifest = {
        "schema_version": "aieb.protocol/v1", "id": protocol_id, "scoring_digest": scoring_digest,
        "max_replacements": 1, "required_trace_coverage": True, "hard_cost_ranking": False,
    }
    cohort = {
        "track": "agents", "suite_id": "rag01", "protocol_id": protocol_id,
        "dependency_mode": "fixture", "hardware_class": "cpu-fixture-standard-v1",
        "budget_profile_id": "browser-budget-v1",
        "application_model_profile": {"dependency_mode": "fixture", "entrypoint": ["python"], "contract_digest": "d" * 64, "model_profile_id": "deterministic-rag-fixture-v1"},
        "required_capabilities": ["cpu-fixture-standard-v1"],
    }
    snapshot = {
        "schema_version": "aieb.analysis/v1", "required_repetitions": 1,
        "per_task": {f"{task_id}:{entrant_id}": {"s": 1, "n": 1, "rate": 1.0, "wilson_95": [0.206543, 1.0], "all_k": True, "pass_power_k": 1.0}},
        "per_entrant": {entrant_id: 1.0}, "per_category": {"rag": {entrant_id: 1.0}},
        "complete_for_rank": True, "suite_rate": 1.0,
        "cost_per_resolution": None, "total_campaign_cost_usd": None, "verifier_cost_total_usd": None,
        "successful_engineering_median_seconds": None, "deadline_rate": 0.0, "infrastructure_attrition": 0.0,
        "per_entrant_valid_trials": {entrant_id: 1}, "per_entrant_resolved_tasks": {entrant_id: 1},
        "per_entrant_total_tasks": {entrant_id: 1}, "per_entrant_cost_per_resolution": {entrant_id: None},
        "per_entrant_verifier_cost_usd": {entrant_id: None}, "per_entrant_median_engineering_seconds": {entrant_id: None},
        "per_entrant_deadline_rate": {entrant_id: 0.0}, "per_entrant_infrastructure_attrition": {entrant_id: 0.0},
        "limitations": [],
    }
    with db.session_factory()() as session:
        evaluator = models.EvaluatorRevisionRow(code_digest="e" * 64, contract_version="browser-v1", review_status="reviewed")
        session.add(evaluator)
        session.flush()
        task = models.TaskRevisionRow(
            slug=task_id, version=task_version, family_id=task_manifest["family_id"], category="rag",
            source_digest="1" * 64, manifest_digest=content_hash(task_manifest), evaluator_id=evaluator.id,
            manifest=task_manifest,
            ticket_text=(ROOT / "suites/dev/rag.document-freshness/instruction.md").read_text(encoding="utf-8").strip(),
        )
        entrant = models.EntrantRevisionRow(
            slug=entrant_id, version="1.0.0", track="agents", config_digest="f" * 64,
            capabilities={"items": ["cpu-fixture-standard-v1"]}, manifest=entrant_manifest,
        )
        protocol = models.ProtocolRevisionRow(version=protocol_id, scoring_digest=scoring_digest, manifest=protocol_manifest)
        session.add_all([task, entrant, protocol])
        session.flush()
        campaign = models.CampaignRow(
            name="ENG-016 browser evidence fixture", state="completed", draft={"schema_version": "browser-fixture"},
            cohort_digest="7" * 64, manifest_digest="8" * 64,
            resolved={"cohort": cohort, "protocol": protocol_manifest, "tasks": [task_manifest], "entrants": [entrant_manifest]},
        )
        session.add(campaign)
        session.flush()
        trial = models.TrialRow(
            campaign_id=campaign.id, task_revision_id=task.id, entrant_revision_id=entrant.id,
            repetition=0, cell_digest="a" * 64,
        )
        session.add(trial)
        session.flush()
        attempt = models.AttemptRow(trial_id=trial.id, number=1, phase="terminal", terminal_status="pass")
        session.add(attempt)
        session.flush()
        candidate_manifest = CandidateManifest(
            schema_version="aieb.candidate/v1", id=uuid4(), base_revision_digest="1" * 64,
            full_tree_hash="2" * 64, files=(),
        )
        candidate = models.CandidateRow(
            attempt_id=attempt.id, tree_digest=candidate_manifest.full_tree_hash,
            manifest_digest=candidate_manifest.digest(), validation_status="valid",
            stored_candidate=_serialize_stored_candidate(StoredCandidate(
                candidate_manifest, (),
                (CandidateDiff("knowledge_service/backend.py", "modify", "--- a/knowledge_service/backend.py\n+++ b/knowledge_service/backend.py\n@@ -1 +1 @@\n-ready = False\n+ready = True\n"),),
                engineering_stdout="browser fixture completed",
            )),
        )
        fixture = models.FixtureRevisionRow(digest="4" * 64, visibility="public", family_id=task.family_id)
        reviewer = models.User(oidc_subject="eng016-browser-reviewer", oidc_issuer="browser-fixture")
        session.add_all([candidate, fixture, reviewer])
        session.flush()
        session.add(models.EvaluationRow(
            candidate_id=candidate.id, evaluator_id=evaluator.id, fixture_id=fixture.id,
            schedule_digest=candidate_manifest.digest(), verdict="pass",
            result={"checks": {"api-ready": True}, "diagnostics": {"summary": "fixture evaluation passed"}},
        ))
        publication = models.PublicationRow(
            campaign_id=campaign.id, snapshot_digest=snapshot_digest(snapshot), snapshot=snapshot,
            reviewer_id=reviewer.id,
        )
        session.add(publication)
        session.commit()
        result = {"publication_id": str(publication.id), "trial_id": str(trial.id), "entrant_id": entrant_id, "task_id": task_id, "task_version": task_version, "protocol_id": protocol_id}

    fixture_path = ROOT / ".cache/eng016-browser/browser-fixture.json"
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    fixture_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
