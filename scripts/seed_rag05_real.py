"""Seed one disposable published run for the REAL RAG-05 vertical.

Ingests the two deterministic local verification campaigns left behind by the
prior working session (`.aieb/runs/rag05-real-{reference,baseline}-local`) into
the local API/Postgres so the web app renders real self-describing task data
instead of static fixtures. These are deterministic control executions (fixed
reference repair vs untouched broken baseline), NOT a model-agent benchmark -
the model-agent track for this task produced no run outcome.

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

from aieb_api import db, models
from aieb_api.publication_evidence import build_evidence_manifest
from aieb_api.snapshots import snapshot_digest
from aieb_api.worker.repository import append_attempt_event
from aieb_api.worker.runner_bridge import _serialize_stored_candidate
from aieb_core.canonical import content_hash
from aieb_core.models import CandidateManifest
from aieb_runner.artifacts import CandidateDiff, StoredCandidate

TASK_DIR = ROOT / "suites" / "real" / "rag.corpus-index-drift"
RUN_DIRS = {"rag05-reference": ROOT / ".aieb" / "runs" / "rag05-real-reference-local",
            "rag05-baseline": ROOT / ".aieb" / "runs" / "rag05-real-baseline-local"}

TASK_MANIFEST = {
    "schema_version": "aieb.task/v1", "id": "rag.corpus-index-drift", "version": "0.1.0",
    "family_id": "knowledge-service-real-a", "category": "rag", "activity": "repair",
    "source": {"repository_digest": "0e55378bd4ccb01a34b03f182b56cb1fdc01082982954d5e59406b5832dc837f",
               "commit": "9d7da06", "license": "Apache-2.0",
               "provenance_digest": "c72a24a55d7a80c3e1fa1d595fcec77153efe4e2498b1d37ba7a612198cc485b"},
    "environment": {"official_image": "registry.example/aieb/rag05@sha256:7777777777777777777777777777777777777777777777777777777777777777",
                    "engineer_cpu": 1, "engineer_memory_mb": 512,
                    "service_topology_digest": "79f99a242457a78c0728d045b845aabb2a59dfdc13077073adb18d0a75490257",
                    "egress_policy": "none"},
    "application": {"dependency_mode": "fixture", "entrypoint": ["python", "-m", "knowledge_service.server"],
                    "contract_digest": "65b87d642953af978ff85624a67247bb052199f4a8f9338e58dd46984b5dffda",
                    "model_profile_id": "deterministic-rag-fixture-v1"},
    "submission": {"include": ["knowledge_service/**"], "protected": ["dev_tests/**"], "max_artifact_bytes": 52428800},
    "requirements": [
        {"id": "api-ready", "severity": "mandatory", "description": "Health returns ready status."},
        {"id": "latest-version-visible", "severity": "mandatory", "description": "Higher versions replace stale searchable text before success response."},
        {"id": "stale-version-absent", "severity": "mandatory", "description": "Superseded version text is no longer searchable."},
        {"id": "idempotent-event-replay", "severity": "mandatory", "description": "Identical accepted events do not duplicate hits."},
        {"id": "lower-version-rejected", "severity": "mandatory", "description": "Lower versions cannot supersede newer state."},
        {"id": "equal-version-conflict", "severity": "mandatory", "description": "Equal version with a different payload conflicts."},
        {"id": "deleted-content-absent", "severity": "mandatory", "description": "Tombstones prevent stale resurrection."},
        {"id": "higher-version-recreation", "severity": "mandatory", "description": "A higher version may recreate a deleted document."},
        {"id": "metadata-filter-respected", "severity": "mandatory", "description": "Supplied metadata filters restrict hits exactly."},
        {"id": "full-text-match", "severity": "mandatory", "description": "Multi-token queries match documents containing all tokens."},
        {"id": "unaffected-documents-preserved", "severity": "mandatory", "description": "Unrelated current records remain searchable."},
        {"id": "incremental-write-scope", "severity": "mandatory", "description": "A mutation may not rebuild unrelated backend records."},
        {"id": "citation-version-mapping", "severity": "mandatory", "description": "Hits cite the current version and chunk ID."},
    ],
    "evaluator": {"evaluator_digest": "589c19ce74e6cbd18588b7e648d23c3a6a07bd7ce03f466ee8dc30720a72d713",
                  "development_fixture": "rag05-dev-v1", "official_fixture_ref": "maintainer-only:rag05-heldout-v1"},
    "profile_compatibility": ["cpu-fixture-standard-v1"],
}

ENTRANTS = [
    {"slug": "rag05-reference", "implementation": "deterministic local reference repair (real corpus-index fix)",
     "tree_hash": "c65f860c62b7fc674aff8c5843ed4bf5b0a3196a9785f8207f8dba97d169af8c"},
    {"slug": "rag05-baseline", "implementation": "deterministic local baseline (broken corpus index untouched)",
     "tree_hash": "dfe08bdf933150f7ce2bdfa3ad9390061238b02ff2dcdb4e0a4b33f265153484"},
]

PROTOCOL_MANIFEST = {
    "schema_version": "aieb.protocol/v1", "id": "rag05-local-verify-v1", "scoring_digest": "8" * 64,
    "max_replacements": 1, "required_trace_coverage": True, "hard_cost_ranking": False,
}

COHORT = {
    "track": "agents", "suite_id": "rag05-real", "protocol_id": PROTOCOL_MANIFEST["id"],
    "dependency_mode": "fixture", "hardware_class": "cpu-fixture-standard-v1",
    "budget_profile_id": "rag05-local-verify-v1",
    "application_model_profile": {"dependency_mode": "fixture", "entrypoint": ["python", "-m", "knowledge_service.server"],
                                  "contract_digest": TASK_MANIFEST["application"]["contract_digest"],
                                  "model_profile_id": "deterministic-rag-fixture-v1"},
    "required_capabilities": ["cpu-fixture-standard-v1"],
}


def _attempt_payload(slug: str) -> dict:
    return json.loads((RUN_DIRS[slug] / "work" / next(
        (p.name for p in (RUN_DIRS[slug] / "work").iterdir() if p.is_dir()), ""
    ) / "attempt.json").read_text(encoding="utf-8"))


def main() -> None:
    db.configure()
    task_id = TASK_MANIFEST["id"]
    task_version = TASK_MANIFEST["version"]
    protocol_id = PROTOCOL_MANIFEST["id"]
    snapshot: dict = {
        "schema_version": "aieb.analysis/v1", "required_repetitions": 1,
        "per_task": {
            f"{task_id}:{entrant['slug']}": {"s": 1, "n": 1, "rate": 1.0, "wilson_95": [0.206549, 1.0], "all_k": True, "pass_power_k": 1.0}
            if entrant["slug"] == "rag05-reference" else {"s": 1, "n": 1, "rate": 0.0, "wilson_95": [0.0, 0.793451], "all_k": True, "pass_power_k": 1.0}
            for entrant in ENTRANTS
        },
        "per_entrant": {entrant["slug"]: (1.0 if entrant["slug"] == "rag05-reference" else 0.0) for entrant in ENTRANTS},
        "per_category": {"rag": {entrant["slug"]: (1.0 if entrant["slug"] == "rag05-reference" else 0.0) for entrant in ENTRANTS}},
        "complete_for_rank": True, "suite_rate": 0.5,
        "cost_per_resolution": None, "total_campaign_cost_usd": None, "verifier_cost_total_usd": None,
        "successful_engineering_median_seconds": None, "deadline_rate": 0.0, "infrastructure_attrition": 0.0,
        "per_entrant_valid_trials": {entrant["slug"]: 1 for entrant in ENTRANTS},
        "per_entrant_resolved_tasks": {entrant["slug"]: 0 for entrant in ENTRANTS},
        "per_entrant_total_tasks": {entrant["slug"]: 1 for entrant in ENTRANTS},
        "per_entrant_cost_per_resolution": {entrant["slug"]: None for entrant in ENTRANTS},
        "per_entrant_verifier_cost_usd": {entrant["slug"]: None for entrant in ENTRANTS},
        "per_entrant_median_engineering_seconds": {entrant["slug"]: None for entrant in ENTRANTS},
        "per_entrant_deadline_rate": {entrant["slug"]: 0.0 for entrant in ENTRANTS},
        "per_entrant_infrastructure_attrition": {entrant["slug"]: 0.0 for entrant in ENTRANTS},
        "limitations": [
            "Deterministic verification controls only; no model agent has attempted this real task yet (RAG-05 model-agent track pending).",
        ],
    }
    snapshot["per_entrant_resolved_tasks"]["rag05-reference"] = 1

    with db.session_factory()() as session:
        evaluator = models.EvaluatorRevisionRow(
            code_digest=TASK_MANIFEST["evaluator"]["evaluator_digest"],
            contract_version="rag05-http-v1", review_status="reviewed",
        )
        session.add(evaluator)
        session.flush()
        task = models.TaskRevisionRow(
            slug=task_id, version=task_version, family_id=TASK_MANIFEST["family_id"], category="rag",
            source_digest=TASK_MANIFEST["source"]["repository_digest"], manifest_digest=content_hash(TASK_MANIFEST),
            evaluator_id=evaluator.id, manifest=TASK_MANIFEST,
            ticket_text=(TASK_DIR / "instruction.md").read_text(encoding="utf-8").strip(),
        )
        entrants: dict[str, models.EntrantRevisionRow] = {}
        for entrant in ENTRANTS:
            slug = entrant["slug"]
            manifest = {
                "schema_version": "aieb.entrant/v1", "id": slug, "track": "agents",
                "agent_implementation": entrant["implementation"], "agent_version": "1.0.0",
                "engineer_model": {"provider_class": "fixture", "requested_model": "deterministic",
                                   "reported_model": "deterministic-local-verify", "settings_digest": "a" * 64},
                "prompt_digest": "b" * 64, "tools_digest": "c" * 64,
                "capabilities": ["cpu-fixture-standard-v1"], "credential_ref_type": "broker",
            }
            entrants[slug] = models.EntrantRevisionRow(
                slug=slug, version="1.0.0", track="agents", config_digest=entrant["tree_hash"],
                capabilities={"items": ["cpu-fixture-standard-v1"]}, manifest=manifest,
            )
        protocol = models.ProtocolRevisionRow(version=protocol_id, scoring_digest=PROTOCOL_MANIFEST["scoring_digest"],
                                               manifest=PROTOCOL_MANIFEST)
        session.add_all([task, *entrants.values(), protocol])
        session.flush()
        campaign = models.CampaignRow(
            name="RAG-05 real-source corpus-index-drift (deterministic controls)", state="completed",
            draft={"schema_version": "rag05-real-local"},
            cohort_digest="7" * 64, manifest_digest="8" * 64,
            resolved={"cohort": COHORT, "protocol": PROTOCOL_MANIFEST,
                      "tasks": [TASK_MANIFEST], "entrants": [entrant.manifest for entrant in entrants.values()]},
        )
        session.add(campaign)
        session.flush()
        fixture = models.FixtureRevisionRow(digest="5" * 64, visibility="public", family_id=task.family_id)
        reviewer = models.User(oidc_subject="rag05-local-reviewer", oidc_issuer="local-verify")
        session.add_all([fixture, reviewer])
        session.flush()

        selected_evaluations: dict = {}
        publications: list[dict] = []
        for entrant in ENTRANTS:
            slug = entrant["slug"]
            payload = _attempt_payload(slug)
            verdict = "pass" if payload["verdict"] == "pass" else "fail"
            trial = models.TrialRow(
                campaign_id=campaign.id, task_revision_id=task.id, entrant_revision_id=entrants[slug].id,
                repetition=0, cell_digest="a" * 64,
            )
            session.add(trial)
            session.flush()
            attempt = models.AttemptRow(trial_id=trial.id, number=1, phase="terminal", terminal_status=verdict)
            session.add(attempt)
            session.flush()
            candidate_manifest = CandidateManifest(
                schema_version="aieb.candidate/v1", id=uuid4(),
                base_revision_digest=payload["candidate_manifest"]["base_revision_digest"],
                full_tree_hash=payload["candidate_manifest"]["full_tree_hash"],
                files=tuple(payload["candidate_manifest"]["files"]),
            )
            diffs: tuple = ()
            if slug == "rag05-reference":
                diffs = (CandidateDiff(
                    "knowledge_service/backend.py", "modify",
                    "reference repair: knowledge_service/backend.py replaced with suites/real/rag.corpus-index-drift/reference/backend.py",
                ),)
            candidate = models.CandidateRow(
                attempt_id=attempt.id, tree_digest=candidate_manifest.full_tree_hash,
                manifest_digest=candidate_manifest.digest(), validation_status="valid",
                stored_candidate=_serialize_stored_candidate(StoredCandidate(candidate_manifest, (), diffs, engineering_stdout="")),
            )
            session.add(candidate)
            session.flush()
            evaluation = models.EvaluationRow(
                candidate_id=candidate.id, evaluator_id=evaluator.id, fixture_id=fixture.id,
                schedule_digest=candidate_manifest.digest(), verdict=verdict,
                result={"checks": dict(payload["evaluation"]["checks"]),
                        "diagnostics": dict(payload["evaluation"]["diagnostics"]),
                        "fixture_version": payload["evaluation"]["fixture_version"], "seed": payload["evaluation"]["seed"]},
            )
            session.add(evaluation)
            session.flush()
            for event_type, event_payload in (
                ("phase.started", {"phase": "engineering"}),
                ("candidate.collected", {"changed_files": len(candidate_manifest.files), "engineering_output_captured": True}),
                ("evaluation.recorded", {"verdict": verdict, "checks": len(payload["evaluation"]["checks"])}),
                ("attempt.terminal", {"terminal_status": verdict, "completed": True}),
            ):
                append_attempt_event(session, attempt_id=attempt.id, event_type=event_type, payload=event_payload)
                session.flush()
            selected_evaluations[trial.id] = evaluation.id
            publications.append({"trial_id": str(trial.id), "entrant_id": slug, "verdict": verdict})

        evidence_manifest = build_evidence_manifest(session, campaign.id, selected_evaluations, snapshot=snapshot)
        publication = models.PublicationRow(
            campaign_id=campaign.id, snapshot_digest=snapshot_digest(snapshot), snapshot=snapshot,
            reviewer_id=reviewer.id, evidence_manifest=evidence_manifest,
        )
        session.add(publication)
        session.commit()
        result = {"publication_id": str(publication.id), "task_id": task_id, "task_version": task_version,
                  "protocol_id": protocol_id, "entrants": publications}

    fixture_path = ROOT / ".cache" / "rag05-real" / "seed.json"
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    fixture_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()