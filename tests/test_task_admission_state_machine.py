"""V2-GAP-003 admission protocol and PostgreSQL state-machine regressions."""

from __future__ import annotations

import os
import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for source in (ROOT / "services/api/src", ROOT / "packages/aieb-core/src"):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from aieb_api import admission  # noqa: E402
from aieb_api.evidence_integrity import evidence_digest, task_revision_digest  # noqa: E402

H64 = "a" * 64


def _executor_report(*, repeats: int = 2, reset_digest: str = H64) -> dict:
    cases = []
    for name, passed in (("baseline", False), ("reference", True),
                         ("alternative", True), ("shortcut", False)):
        for repetition in range(repeats):
            cases.append({
                "case_id": name,
                "repetition_index": repetition,
                "passed": passed,
                "outcome_digest": evidence_digest({"case": name, "repetition": repetition}),
            })
    resets = [
        {"matrix_case_id": name, "reset_number": number + 1,
         "clean_digest": reset_digest, "status": "pass"}
        for name in ("baseline", "reference", "alternative", "shortcut")
        for number in range(10)
    ]
    gates = [
        {"gate_name": name, "status": "pass", "observed_digest": H64}
        for name in admission._EXECUTOR_GATE_NAMES  # noqa: SLF001 - protocol-bound test
    ]
    return {"gates": gates, "cases": cases, "resets": resets}


class AdmissionProtocolUnitTests(unittest.TestCase):
    def test_report_requires_every_pinned_repetition(self) -> None:
        report = _executor_report()
        report["cases"] = [entry for entry in report["cases"]
                           if not (entry["case_id"] == "reference" and entry["repetition_index"] == 1)]
        outcome = admission.parse_executor_report(report, admission.resolve_protocol(None))
        self.assertEqual(len([case for case in outcome.cases if case.case_id == "reference"]), 1)

    def test_duplicate_case_repetition_is_rejected(self) -> None:
        report = _executor_report()
        report["cases"].append(dict(report["cases"][0]))
        with self.assertRaises(admission.AdmissionExecutorError):
            admission.parse_executor_report(report, admission.resolve_protocol(None))

    def test_duplicate_reset_number_is_rejected(self) -> None:
        report = _executor_report()
        duplicate = dict(report["resets"][0])
        report["resets"].append(duplicate)
        with self.assertRaises(admission.AdmissionExecutorError):
            admission.parse_executor_report(report, admission.resolve_protocol(None))

    def test_reset_gate_requires_contiguous_numbers_from_one(self) -> None:
        protocol = admission.resolve_protocol(None)
        resets = tuple(
            admission.ResetObservation("baseline", number, H64, "pass")
            for number in range(2, protocol.min_resets + 2)
        )
        gate = admission._clean_reset_gate(  # noqa: SLF001 - protocol-bound regression
            list(resets), protocol, expected_clean_digest=H64, matrix_case_ids={"baseline"}
        )
        self.assertEqual(gate.status, "fail")

    def test_unknown_gate_is_rejected(self) -> None:
        report = _executor_report()
        report["gates"].append({"gate_name": "invented", "status": "pass"})
        with self.assertRaises(admission.AdmissionExecutorError):
            admission.parse_executor_report(report, admission.resolve_protocol(None))

    def test_executor_diagnostics_redact_secret_shaped_fields(self) -> None:
        report = _executor_report()
        report["gates"][0]["details"] = {
            "token": "do-not-persist",
            "nested": {"password": "also-do-not-persist", "safe": "kept"},
        }
        outcome = admission.parse_executor_report(report, admission.resolve_protocol(None))
        details = outcome.gates[0].details
        self.assertEqual(details["token"], "<redacted>")
        self.assertEqual(details["nested"]["password"], "<redacted>")
        self.assertEqual(details["nested"]["safe"], "kept")


DATABASE_URL = os.environ.get("AIEB_DATABASE_URL")

if DATABASE_URL:
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    from aieb_api import db, models
    from aieb_api.errors import ApiError
    from aieb_core.models import (
        Activity, ApplicationProfile, Category, DependencyMode, EnvironmentSpec,
        EvaluatorRef, Requirement, SourceRef, SubmissionPolicy, TaskRevision,
    )


@unittest.skipUnless(DATABASE_URL, "AIEB_DATABASE_URL is not set; admission persistence requires PostgreSQL")
class AdmissionPersistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        db.configure(DATABASE_URL)

    def setUp(self) -> None:
        with db.engine().begin() as connection:
            for table in reversed(models.Base.metadata.sorted_tables):
                connection.execute(text(f'TRUNCATE TABLE "{table.name}" CASCADE'))

    def _seed(self):
        # Everything from session creation through the final commit must be
        # exception-safe (review finding, 2026-09-22): a validation error
        # raised anywhere in here used to propagate out of `_seed()` itself,
        # i.e. BEFORE the caller's own `try/finally: session.close()` ever
        # started - leaking an open, already-flushed transaction that then
        # blocked every later test's `TRUNCATE` in setUp() (lock contention
        # on `users`/`evaluator_revision`), hanging the rest of the suite
        # instead of failing this one test.
        session = db.session_factory()()
        try:
            author = models.User(oidc_issuer="test", oidc_subject="author")
            reviewer = models.User(oidc_issuer="test", oidc_subject="reviewer")
            session.add_all((author, reviewer))
            session.flush()
            evaluator = models.EvaluatorRevisionRow(
                code_digest=H64, contract_version="v1", review_status="pending-independent-review")
            session.add(evaluator)
            session.flush()
            task = TaskRevision(
                schema_version="aieb.task/v1", id="rag.admission-test", version="1.0.0",
                family_id="admission-family", category=Category.RAG, activity=Activity.REPAIR,
                source=SourceRef(repository_digest=H64, commit="pinned-revision", license="Apache-2.0",
                                 provenance_digest=H64),
                environment=EnvironmentSpec(
                    official_image="registry.example/aieb@sha256:" + H64,
                    engineer_cpu=1, engineer_memory_mb=512,
                    service_topology_digest=H64, egress_policy="none"),
                application=ApplicationProfile(
                    dependency_mode=DependencyMode.FIXTURE, entrypoint=("python", "-m", "service"),
                    contract_digest=H64, model_profile_id="fixture"),
                submission=SubmissionPolicy(include=("src/",), protected=("tests/",),
                                            max_artifact_bytes=100_000),
                requirements=(Requirement(id="r1", severity="mandatory", description="works"),),
                evaluator=EvaluatorRef(evaluator_digest=H64, development_fixture="dev",
                                       official_fixture_ref="private://fixture"),
                profile_compatibility=("cohort",),
            )
            manifest = task.model_dump(mode="json")
            revision = models.TaskRevisionRow(
                slug=task.id, version=task.version, family_id=task.family_id,
                category=task.category.value, source_digest=task.source.repository_digest,
                manifest_digest=task.digest(), evaluator_id=evaluator.id, manifest=manifest,
                ticket_text="Fix the behavior", revision_digest=task_revision_digest(manifest, "Fix the behavior"),
            )
            session.add(revision)
            session.flush()
            session.add(models.TaskAdmissionStateRow(
                task_revision_id=revision.id, status="frozen", author_user_id=author.id))
            session.commit()
        except BaseException:
            session.rollback()
            session.close()
            raise
        return session, author, reviewer, revision

    def test_full_pass_requires_independent_review_before_release_eligibility(self) -> None:
        session, author, reviewer, revision = self._seed()
        try:
            protocol = admission.resolve_protocol(None)
            run = admission.start_run(session, revision=revision, requester_id=author.id, protocol=protocol)
            admission.claim_for_execution(session, run.id)
            admission.begin_execution_state(session, revision.id)
            outcome = admission.parse_executor_report(
                _executor_report(reset_digest=revision.source_digest), protocol)
            self.assertEqual(admission.finalize_run(
                session, run=run, protocol=protocol, outcome=outcome,
                failure_reason=None, actor_user_id=author.id), "passed")
            session.refresh(run)
            self.assertIsNotNone(admission.release_eligibility_error(session, [revision.id]))
            admission.record_review(
                session, run=run, reviewer_id=reviewer.id, decision="approve",
                scope="task-admission", evidence_digest_value=run.result_digest,
                independence_declaration=True, reason="independent evidence review")
            session.commit()
            self.assertIsNone(admission.release_eligibility_error(session, [revision.id]))
        finally:
            session.close()

    def test_requester_cannot_review_own_run(self) -> None:
        session, author, _reviewer, revision = self._seed()
        try:
            review = admission.seed_fixture_admission
            with self.assertRaises(ApiError):
                # The service performs this check before any review row exists.
                protocol = admission.resolve_protocol(None)
                run = admission.start_run(session, revision=revision, requester_id=author.id, protocol=protocol)
                run.status = "passed"
                run.result_digest = H64
                state = admission.admission_state(session, revision.id)
                state.status = "admission_pending"
                session.flush()
                state.status = "admission_running"
                session.flush()
                state.status = "pending_independent_review"
                session.flush()
                admission.record_review(
                    session, run=run, reviewer_id=author.id, decision="approve",
                    scope="task-admission", evidence_digest_value=H64,
                    independence_declaration=True, reason="self review")
            _ = review
        finally:
            session.rollback()
            session.close()

    def test_terminal_run_rejects_mutation(self) -> None:
        session, author, _reviewer, revision = self._seed()
        try:
            run = models.TaskAdmissionRunRow(
                task_revision_id=revision.id, status="pending", requested_by_user_id=author.id,
                protocol_version="aieb.admission-protocol/v1", protocol_digest=H64,
                revision_digest=revision.revision_digest, manifest_digest=revision.manifest_digest,
                source_digest=revision.source_digest, evaluator_digest=H64)
            session.add(run)
            session.commit()
            run.status = "running"
            session.commit()
            run.status = "passed"
            run.result_digest = H64
            session.commit()
            run.failure_reason = "rewrite"
            with self.assertRaises(IntegrityError):
                session.commit()
        finally:
            session.rollback()
            session.close()


if __name__ == "__main__":
    unittest.main()
