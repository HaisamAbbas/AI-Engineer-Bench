"""V2-GAP-003: the task admission state machine, gates, and release eligibility.

Freezing a draft only pins immutable revision identity. Admission is a separate,
persisted, fail-closed lifecycle:

    draft -> frozen -> admission_pending -> admission_running -> admission_failed
                                       -> pending_independent_review -> admitted/rejected

Design rules enforced here and in migration 6f2a9d5c1e73:

- The set of mandatory gates, required matrix cases, reset count, and matrix
  repeats come from a versioned *protocol* (`ADMISSION_PROTOCOLS`), never from
  a route. A run pins its protocol version and protocol digest.
- This module never executes evaluator code. Behavioral evidence arrives
  through an `AdmissionExecutor` boundary (a subprocess adapter such as
  scripts/admission_execute_local.py); this module classifies the reported
  verdicts into gates. Missing evidence is a failure, never a pass.
- A run may only pass when EVERY mandatory gate exists and passes. Partial
  runs cannot become admitted.
- Only an independent reviewer (distinct from the revision author and the run
  requester) may move `pending_independent_review` to `admitted`/`rejected`.
- Only `admitted` revisions are release/campaign eligible, and eligibility
  re-verifies that the admitted digests still match the stored revision.
- Stored evidence is bounded: digests, counts, references, and redacted
  diagnostics. Private fixture content is never persisted or returned.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol as TypingProtocol
from uuid import UUID

from aieb_core.models import TaskRevision
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .errors import conflict, invalid_request
from .evidence_integrity import evidence_digest, task_revision_digest
from .models import (
    ADMISSION_GATE_STATUSES,
    EvaluatorRevisionRow,
    TaskAdmissionGateRow,
    TaskAdmissionResetRow,
    TaskAdmissionReviewRow,
    TaskAdmissionRunRow,
    TaskAdmissionStateRow,
    TaskRevisionRow,
    User,
)

# ---------------------------------------------------------------------------
# Protocol: what "admitted" requires. Versioned; a run pins the exact version.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GateSpec:
    name: str
    required: bool
    description: str


@dataclass(frozen=True)
class AdmissionProtocol:
    version: str
    gates: tuple[GateSpec, ...]
    required_matrix_cases: tuple[str, ...]
    min_resets: int
    matrix_repeats: int

    @property
    def gate_names(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in self.gates)

    @property
    def required_gate_names(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in self.gates if spec.required)

    def digest(self) -> str:
        return evidence_digest({
            "schema_version": "aieb.admission-protocol-identity/v1",
            "version": self.version,
            "gates": [{"name": spec.name, "required": spec.required} for spec in self.gates],
            "required_matrix_cases": list(self.required_matrix_cases),
            "min_resets": self.min_resets,
            "matrix_repeats": self.matrix_repeats,
        })


_GATE_DESCRIPTIONS = {
    "manifest_schema": "canonical TaskRevision validation, real content digests, license/provenance, evaluator and protocol identity",
    "clean_checkout": "reconstruct the exact task source from its pinned digest in a clean workspace",
    "baseline_behavior": "the baseline must FAIL the intended target requirements",
    "reference_behavior": "the reference implementation must pass all required checks",
    "independent_alternative": "an independent alternative implementation passes without the reference or an answer artifact",
    "shortcut_adversarial_controls": "shortcut candidates fail; adversarial candidates do not pass through evaluator loopholes",
    "public_hidden_consistency": "every hidden evaluator requirement maps to the published task contract",
    "private_fixture_leakage": "no private evaluator/holdout path or content in the public package or build context",
    "clean_reset_reproducibility": "reset between matrix cases; repeat the required resets with matching clean digests",
    "evaluator_determinism": "repeated matrix runs produce identical verdicts and outcome digests",
    "evidence_completeness": "every required matrix case and reset has bounded evidence; nothing missing counts as a pass",
}


def _protocol(version: str, *, min_resets: int = 10, matrix_repeats: int = 2) -> AdmissionProtocol:
    return AdmissionProtocol(
        version=version,
        gates=tuple(GateSpec(name=name, required=True, description=description) for name, description in _GATE_DESCRIPTIONS.items()),
        required_matrix_cases=("baseline", "reference", "alternative"),
        min_resets=min_resets,
        matrix_repeats=matrix_repeats,
    )


ADMISSION_PROTOCOLS: dict[str, AdmissionProtocol] = {
    "aieb.admission-protocol/v1": _protocol("aieb.admission-protocol/v1"),
}
CURRENT_PROTOCOL_VERSION = "aieb.admission-protocol/v1"


def get_protocol(version: str) -> AdmissionProtocol | None:
    return ADMISSION_PROTOCOLS.get(version)


def resolve_protocol(version: str | None) -> AdmissionProtocol:
    """Fail closed on an unknown protocol: an unpinned gate definition is not
    an admission criterion."""
    candidate = version or CURRENT_PROTOCOL_VERSION
    protocol = ADMISSION_PROTOCOLS.get(candidate)
    if protocol is None:
        raise invalid_request(f"unknown admission protocol version: {candidate}")
    return protocol


# ---------------------------------------------------------------------------
# Executor boundary: behavioral evidence arrives from outside this process.
# ---------------------------------------------------------------------------

_EXECUTOR_GATE_NAMES = ("clean_checkout", "public_hidden_consistency", "private_fixture_leakage", "evaluator_determinism")
_MAX_EXECUTOR_OUTPUT_BYTES = 4 * 1024 * 1024
_MAX_CASES = 64
_MAX_RESETS = 2048
_MAX_GATES = 32
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_SECRET_KEY_RE = re.compile(r"(?:token|password|secret|credential|authorization|api[_-]?key|private[_-]?key)", re.I)
_SECRET_TEXT_RE = re.compile(
    r"(?i)(bearer\s+|(?:sk|ghp|xox[baprs]|AKIA|AIza)[-_A-Za-z0-9]{8,}|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)"
)


class AdmissionExecutorError(RuntimeError):
    """Infrastructure failure while collecting admission evidence. A run that
    hits this fails; it never silently passes a gate."""


class AdmissionExecutor(TypingProtocol):
    def execute(self, context: "AdmissionContext") -> "ExecutorOutcome": ...


@dataclass(frozen=True)
class AdmissionContext:
    revision_id: str
    slug: str
    version: str
    revision_digest: str
    manifest_digest: str
    source_digest: str
    evaluator_digest: str
    manifest: dict[str, Any]
    ticket_text: str | None
    protocol_version: str
    protocol_digest: str
    required_matrix_cases: tuple[str, ...]
    executor_gate_names: tuple[str, ...]
    min_resets: int
    matrix_repeats: int

    def to_payload(self) -> dict[str, Any]:
        """Bounded stdin payload for the external executor. Contains the public
        manifest only - never private fixture content."""
        return {
            "schema_version": "aieb.admission-execution-request/v1",
            "revision_id": self.revision_id,
            "slug": self.slug,
            "version": self.version,
            "revision_digest": self.revision_digest,
            "manifest_digest": self.manifest_digest,
            "source_digest": self.source_digest,
            "evaluator_digest": self.evaluator_digest,
            "manifest": self.manifest,
            "ticket_digest": evidence_digest({"ticket_text": self.ticket_text}),
            "protocol": {
                "version": self.protocol_version,
                "digest": self.protocol_digest,
                "required_matrix_cases": list(self.required_matrix_cases),
                "executor_gates": list(self.executor_gate_names),
                "min_resets": self.min_resets,
                "matrix_repeats": self.matrix_repeats,
            },
        }


@dataclass(frozen=True)
class GateObservation:
    gate_name: str
    status: str
    observed_digest: str | None = None
    evidence_reference: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CaseObservation:
    case_id: str
    repetition_index: int
    passed: bool
    outcome_digest: str
    checks_passed: int | None = None
    checks_total: int | None = None
    notes: str | None = None


@dataclass(frozen=True)
class ResetObservation:
    matrix_case_id: str
    reset_number: int
    clean_digest: str
    status: str
    evidence_reference: str | None = None


@dataclass(frozen=True)
class ExecutorOutcome:
    gates: tuple[GateObservation, ...]
    cases: tuple[CaseObservation, ...]
    resets: tuple[ResetObservation, ...]


class CommandAdmissionExecutor:
    """Runs the configured external executor command (the runner/Harbor adapter
    boundary) and parses its bounded JSON report.

    The API never reimplements evaluator semantics: the command executes the
    matrix in a clean workspace and reports verdicts, digests, and reset
    evidence. Anything malformed, oversized, or timed out is an infrastructure
    failure - the run fails closed.
    """

    def __init__(self, command: str, *, timeout_seconds: int = 900) -> None:
        self.command = command
        self.timeout_seconds = timeout_seconds

    def execute(self, context: AdmissionContext) -> ExecutorOutcome:
        argv = shlex.split(self.command, posix=os.name != "nt")
        payload = json.dumps(context.to_payload(), sort_keys=True).encode("utf-8")
        try:
            completed = subprocess.run(
                argv, input=payload, capture_output=True, timeout=self.timeout_seconds, check=False,
            )
        except (OSError, ValueError) as exc:
            raise AdmissionExecutorError(f"admission executor could not be started: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise AdmissionExecutorError(f"admission executor exceeded its {self.timeout_seconds}s deadline") from exc
        if len(completed.stdout) > _MAX_EXECUTOR_OUTPUT_BYTES:
            raise AdmissionExecutorError("admission executor report exceeded the bounded output limit")
        if completed.returncode != 0:
            stderr = completed.stderr[:2000].decode("utf-8", errors="replace") if completed.stderr else ""
            raise AdmissionExecutorError(
                f"admission executor exited with code {completed.returncode}: {stderr.strip() or 'no diagnostics'}"
            )
        try:
            report = json.loads(completed.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AdmissionExecutorError("admission executor did not produce a JSON report") from exc
        return parse_executor_report(report, context)


def parse_executor_report(report: Any, context: AdmissionProtocol | AdmissionContext) -> ExecutorOutcome:
    """Validate and bound an executor report. Unknown gate names, malformed
    digests, or wrong statuses are rejected rather than trusted."""
    if not isinstance(report, dict):
        raise AdmissionExecutorError("admission executor report must be a JSON object")
    if report.get("error"):
        raise AdmissionExecutorError(f"admission executor reported failure: {str(report['error'])[:500]}")
    allowed_gates = set(_EXECUTOR_GATE_NAMES)
    raw_gates = report.get("gates", [])
    raw_cases = report.get("cases", [])
    raw_resets = report.get("resets", [])
    if not isinstance(raw_gates, list) or not isinstance(raw_cases, list) or not isinstance(raw_resets, list):
        raise AdmissionExecutorError("admission executor report gates/cases/resets must be lists")
    if len(raw_gates) > _MAX_GATES or len(raw_cases) > _MAX_CASES or len(raw_resets) > _MAX_RESETS:
        raise AdmissionExecutorError("admission executor report exceeds bounded entry limits")

    gates: list[GateObservation] = []
    for entry in raw_gates:
        if not isinstance(entry, dict):
            raise AdmissionExecutorError("gate entries must be objects")
        name = entry.get("gate_name")
        status = entry.get("status")
        if name not in allowed_gates:
            raise AdmissionExecutorError(f"executor reported an unknown gate: {str(name)[:64]}")
        if status not in ("pass", "fail", "indeterminate"):
            raise AdmissionExecutorError(f"executor reported an invalid status for gate {name}: {str(status)[:32]}")
        observed = entry.get("observed_digest")
        if observed is not None and not (isinstance(observed, str) and _DIGEST_RE.match(observed)):
            raise AdmissionExecutorError(f"gate {name} observed_digest must be a sha256 hex digest")
        gates.append(GateObservation(
            gate_name=name, status=status,
            observed_digest=observed,
            evidence_reference=_bounded_text(entry.get("evidence_reference"), 500),
            details=_bounded_json(entry.get("details") or {}),
        ))

    cases: list[CaseObservation] = []
    seen_case_repetitions: set[tuple[str, int]] = set()
    for entry in raw_cases:
        if not isinstance(entry, dict):
            raise AdmissionExecutorError("case entries must be objects")
        case_id = entry.get("case_id")
        outcome = entry.get("outcome_digest")
        if not isinstance(case_id, str) or not case_id or len(case_id) > 64:
            raise AdmissionExecutorError("case_id must be a bounded non-empty string")
        if not isinstance(entry.get("passed"), bool):
            raise AdmissionExecutorError(f"case {case_id} must report a boolean verdict")
        repetition_index = entry.get("repetition_index")
        if not isinstance(repetition_index, int) or isinstance(repetition_index, bool) or repetition_index < 0:
            raise AdmissionExecutorError(f"case {case_id} must report a nonnegative repetition_index")
        if repetition_index >= context.matrix_repeats:
            raise AdmissionExecutorError(
                f"case {case_id} repetition_index exceeds the pinned protocol repeat count"
            )
        case_key = (case_id, repetition_index)
        if case_key in seen_case_repetitions:
            raise AdmissionExecutorError(f"case {case_id} repetition {repetition_index} was reported twice")
        seen_case_repetitions.add(case_key)
        if not (isinstance(outcome, str) and _DIGEST_RE.match(outcome)):
            raise AdmissionExecutorError(f"case {case_id} must report an outcome_digest")
        cases.append(CaseObservation(
            case_id=case_id, repetition_index=repetition_index,
            passed=entry["passed"], outcome_digest=outcome,
            checks_passed=_bounded_int(entry.get("checks_passed")),
            checks_total=_bounded_int(entry.get("checks_total")),
            notes=_bounded_text(entry.get("notes"), 500),
        ))

    resets: list[ResetObservation] = []
    seen_reset_keys: set[tuple[str, int]] = set()
    for entry in raw_resets:
        if not isinstance(entry, dict):
            raise AdmissionExecutorError("reset entries must be objects")
        case_id = entry.get("matrix_case_id")
        clean = entry.get("clean_digest")
        status = entry.get("status")
        number = entry.get("reset_number")
        if not isinstance(case_id, str) or not case_id or len(case_id) > 64:
            raise AdmissionExecutorError("reset matrix_case_id must be a bounded non-empty string")
        if not (isinstance(clean, str) and _DIGEST_RE.match(clean)):
            raise AdmissionExecutorError(f"reset for {case_id} must report a sha256 clean_digest")
        if status not in ("pass", "fail"):
            raise AdmissionExecutorError(f"reset for {case_id} must report pass/fail")
        if not isinstance(number, int) or not 0 < number <= _MAX_RESETS:
            raise AdmissionExecutorError(f"reset for {case_id} must report a positive reset_number")
        reset_key = (case_id, number)
        if reset_key in seen_reset_keys:
            raise AdmissionExecutorError(f"reset for {case_id} number {number} was reported more than once")
        seen_reset_keys.add(reset_key)
        resets.append(ResetObservation(
            matrix_case_id=case_id, reset_number=number, clean_digest=clean, status=status,
            evidence_reference=_bounded_text(entry.get("evidence_reference"), 500),
        ))

    return ExecutorOutcome(gates=tuple(gates), cases=tuple(cases), resets=tuple(resets))


def configured_executor() -> CommandAdmissionExecutor | None:
    command = os.environ.get("AIEB_ADMISSION_EXECUTOR", "").strip()
    if not command:
        return None
    timeout = os.environ.get("AIEB_ADMISSION_EXECUTOR_TIMEOUT_SECONDS", "900")
    try:
        timeout_seconds = int(timeout)
    except ValueError as timeout_error:
        raise AdmissionExecutorError(f"AIEB_ADMISSION_EXECUTOR_TIMEOUT_SECONDS must be an integer: {timeout[:32]}") from timeout_error
    if not 1 <= timeout_seconds <= 3600:
        raise AdmissionExecutorError("AIEB_ADMISSION_EXECUTOR_TIMEOUT_SECONDS must be within 1..3600")
    return CommandAdmissionExecutor(command, timeout_seconds=timeout_seconds)


# ---------------------------------------------------------------------------
# Bounded evidence helpers.
# ---------------------------------------------------------------------------


def _bounded_text(value: Any, limit: int = 500) -> str | None:
    if value is None:
        return None
    text = str(value)
    text = _SECRET_TEXT_RE.sub("<redacted>", text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _bounded_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 1_000_000 else None


def _bounded_json(value: Any, *, depth: int = 0) -> dict[str, Any]:
    """Redacted, bounded diagnostics: no raw fixture content, no unbounded blobs."""
    if not isinstance(value, dict) or depth > 2:
        return {}
    bounded: dict[str, Any] = {}
    for index, (key, item) in enumerate(value.items()):
        if index >= 20:
            bounded["truncated"] = True
            break
        key_text = str(key)[:64]
        if _SECRET_KEY_RE.search(key_text):
            bounded[key_text] = "<redacted>"
            continue
        if isinstance(item, dict):
            bounded[key_text] = _bounded_json(item, depth=depth + 1)
        elif isinstance(item, list):
            bounded[key_text] = [_bounded_text(entry, 200) for entry in item[:10]]
        elif isinstance(item, bool) or isinstance(item, int):
            bounded[key_text] = item
        else:
            bounded[key_text] = _bounded_text(item, 500)
    return bounded


# ---------------------------------------------------------------------------
# State reads and run creation.
# ---------------------------------------------------------------------------


def admission_state(session: Session, revision_id: UUID) -> TaskAdmissionStateRow | None:
    return session.get(TaskAdmissionStateRow, revision_id)


def current_admission_status(session: Session, revision_id: UUID) -> str:
    state = admission_state(session, revision_id)
    return state.status if state is not None else "frozen"


def latest_run(session: Session, revision_id: UUID) -> TaskAdmissionRunRow | None:
    return session.execute(
        select(TaskAdmissionRunRow)
        .where(TaskAdmissionRunRow.task_revision_id == revision_id)
        .order_by(TaskAdmissionRunRow.created_at.desc(), TaskAdmissionRunRow.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def active_run(session: Session, revision_id: UUID) -> TaskAdmissionRunRow | None:
    return session.execute(
        select(TaskAdmissionRunRow).where(
            TaskAdmissionRunRow.task_revision_id == revision_id,
            TaskAdmissionRunRow.status.in_(("pending", "running")),
        )
    ).scalar_one_or_none()


def _evaluator_digest(session: Session, revision: TaskRevisionRow) -> str:
    evaluator = session.get(EvaluatorRevisionRow, revision.evaluator_id)
    if evaluator is None:
        raise conflict("the task revision references no evaluator revision")
    return evaluator.code_digest


def start_run(
    session: Session, *, revision: TaskRevisionRow, requester_id: UUID, protocol: AdmissionProtocol
) -> TaskAdmissionRunRow:
    """Create an admission run with one `not_run` row per protocol gate and
    move the revision to `admission_pending`.

    Requires a frozen revision identity (an admission state row) and rejects a
    second active run - both in SQL and via the partial unique index.
    """
    state = admission_state(session, revision.id)
    if state is None:
        raise conflict("this task revision has no admission state; freeze it through authoring before admission")
    if state.status not in ("frozen", "admission_failed", "pending_independent_review"):
        raise conflict(f"task revision is in state {state.status}; a new admission run is not allowed from there")
    existing = active_run(session, revision.id)
    if existing is not None:
        raise conflict("an admission run is already active for this task revision")

    run = TaskAdmissionRunRow(
        task_revision_id=revision.id,
        status="pending",
        requested_by_user_id=requester_id,
        protocol_version=protocol.version,
        protocol_digest=protocol.digest(),
        revision_digest=revision.revision_digest,
        manifest_digest=revision.manifest_digest,
        source_digest=revision.source_digest,
        evaluator_digest=_evaluator_digest(session, revision),
    )
    session.add(run)
    session.flush()  # the partial unique index resolves concurrent creators here
    for spec in protocol.gates:
        session.add(TaskAdmissionGateRow(
            admission_run_id=run.id, gate_name=spec.name, required=spec.required,
            status="not_run", details={},
        ))
    state.status = "admission_pending"
    state.failure_reason = None
    session.flush()
    return run


def claim_for_execution(session: Session, run_id: UUID) -> TaskAdmissionRunRow | None:
    """Atomically move pending -> running. Only one caller can win; losers get
    None and must replay (same idempotency key) or conflict."""
    return session.execute(
        update(TaskAdmissionRunRow)
        .where(TaskAdmissionRunRow.id == run_id, TaskAdmissionRunRow.status == "pending")
        .values(status="running", started_at=datetime.now(timezone.utc))
        .returning(TaskAdmissionRunRow)
    ).scalar_one_or_none()


def begin_execution_state(session: Session, revision_id: UUID) -> TaskAdmissionStateRow:
    state = admission_state(session, revision_id)
    if state is None or state.status != "admission_pending":
        found = state.status if state is not None else "missing"
        raise conflict(f"task revision admission state is {found}; expected admission_pending")
    state.status = "admission_running"
    session.flush()
    return state


def build_context(session: Session, run: TaskAdmissionRunRow, protocol: AdmissionProtocol) -> AdmissionContext:
    revision = session.get(TaskRevisionRow, run.task_revision_id)
    if revision is None:
        raise conflict("the admission run references a task revision that no longer exists")
    if run.protocol_digest != protocol.digest():
        raise conflict("the admission run protocol digest does not match its pinned protocol version")
    return AdmissionContext(
        revision_id=str(revision.id),
        slug=revision.slug,
        version=revision.version,
        revision_digest=revision.revision_digest,
        manifest_digest=revision.manifest_digest,
        source_digest=revision.source_digest,
        evaluator_digest=run.evaluator_digest,
        manifest=revision.manifest,
        ticket_text=revision.ticket_text,
        protocol_version=protocol.version,
        protocol_digest=protocol.digest(),
        required_matrix_cases=protocol.required_matrix_cases,
        executor_gate_names=_EXECUTOR_GATE_NAMES,
        min_resets=protocol.min_resets,
        matrix_repeats=protocol.matrix_repeats,
    )


# ---------------------------------------------------------------------------
# Gate classification: verdicts in, gates out. This module never runs an
# evaluator; it classifies what the executor reported (spec: candidate failures
# are data, infrastructure failures are disclosed separately).
# ---------------------------------------------------------------------------


def _manifest_schema_gate(session: Session, revision: TaskRevisionRow, protocol: AdmissionProtocol) -> GateObservation:
    reasons: list[str] = []
    try:
        manifest = TaskRevision.model_validate(revision.manifest)
    except ValidationError as exc:
        return GateObservation(
            "manifest_schema", "fail", None, None,
            {"reasons": [f"manifest failed canonical validation: {error['loc']}" for error in exc.errors()[:5]]},
        )
    if task_revision_digest(revision.manifest, revision.ticket_text) != revision.revision_digest:
        reasons.append("revision_digest does not match the stored manifest/ticket identity")
    if manifest.digest() != revision.manifest_digest:
        reasons.append("manifest_digest does not match the canonical manifest digest")
    source = revision.manifest.get("source") if isinstance(revision.manifest, dict) else None
    if not isinstance(source, dict):
        reasons.append("manifest has no source block")
    else:
        if not str(source.get("license") or "").strip() or str(source.get("license")) == "unspecified":
            reasons.append("source license metadata is missing")
        if not _DIGEST_RE.match(str(source.get("provenance_digest") or "")):
            reasons.append("source provenance_digest is not a sha256 digest")
        if not _DIGEST_RE.match(str(source.get("repository_digest") or "")):
            reasons.append("source repository_digest is not a sha256 digest")
    evaluator = revision.manifest.get("evaluator") if isinstance(revision.manifest, dict) else None
    if not isinstance(evaluator, dict) or not _DIGEST_RE.match(str(evaluator.get("evaluator_digest") or "")):
        reasons.append("evaluator identity (evaluator_digest) is missing or malformed")
    if session.get(EvaluatorRevisionRow, revision.evaluator_id) is None:
        reasons.append("evaluator revision row is missing")
    if get_protocol(protocol.version) is None:
        reasons.append("admission protocol identity is unknown")
    if reasons:
        return GateObservation("manifest_schema", "fail", None, None, {"reasons": reasons[:10]})
    return GateObservation(
        "manifest_schema", "pass", revision.manifest_digest, None,
        {"protocol_version": protocol.version, "protocol_digest": protocol.digest(),
         "license": str(source.get("license")), "provenance_digest": str(source.get("provenance_digest"))},
    )


def _case_gate(
    name: str, cases: dict[str, list[CaseObservation]], *, expect_pass: bool,
    required_repeats: int,
) -> GateObservation:
    observations = cases.get(name, [])
    by_repetition = {case.repetition_index: case for case in observations}
    if len(by_repetition) < required_repeats or any(index not in by_repetition for index in range(required_repeats)):
        return GateObservation(name and _GATE_CASE_GATE.get(name, name), "fail", None, None,
                               {"reason": f"matrix case '{name}' did not report all {required_repeats} repetitions"})
    selected = [by_repetition[index] for index in range(required_repeats)]
    verdict = "pass" if all(case.passed == expect_pass for case in selected) else "fail"
    return GateObservation(
        _GATE_CASE_GATE.get(name, name), verdict,
        evidence_digest({case.repetition_index: case.outcome_digest for case in selected}), None,
        {"case_id": name, "reported_verdicts": [case.passed for case in selected],
         "expected_verdict": expect_pass, "repetitions": required_repeats},
    )


_GATE_CASE_GATE = {
    "baseline": "baseline_behavior",
    "reference": "reference_behavior",
    "alternative": "independent_alternative",
}


def _shortcut_gate(cases: dict[str, list[CaseObservation]], required: tuple[str, ...]) -> GateObservation:
    controls = [case for case_list in cases.values() for case in case_list if case.case_id not in required]
    if not controls:
        return GateObservation(
            "shortcut_adversarial_controls", "fail", None, None,
            {"reason": "no shortcut/adversarial control cases were reported"},
        )
    passing = [case.case_id for case in controls if case.passed]
    if passing:
        return GateObservation(
            "shortcut_adversarial_controls", "fail", None, None,
            {"passing_controls": sorted(passing)[:10],
             "reason": "shortcut/adversarial control candidates must fail"},
        )
    digests = evidence_digest({f"{case.case_id}:{case.repetition_index}": case.outcome_digest
                               for case in sorted(controls, key=lambda item: (item.case_id, item.repetition_index))})
    return GateObservation(
        "shortcut_adversarial_controls", "pass", digests, None,
        {"control_cases": sorted(case.case_id for case in controls)[:20]},
    )


def _clean_reset_gate(
    resets: list[ResetObservation], protocol: AdmissionProtocol, *, expected_clean_digest: str,
    matrix_case_ids: set[str],
) -> GateObservation:
    expected_numbers = set(range(1, protocol.min_resets + 1))
    numbers_by_case: dict[str, set[int]] = {case_id: set() for case_id in matrix_case_ids}
    for reset in resets:
        if reset.matrix_case_id in numbers_by_case:
            numbers_by_case[reset.matrix_case_id].add(reset.reset_number)
    insufficient = {
        case_id: sorted(expected_numbers - numbers)
        for case_id, numbers in numbers_by_case.items()
        if not expected_numbers.issubset(numbers)
    }
    if insufficient:
        return GateObservation(
            "clean_reset_reproducibility", "fail", None, None,
            {"reset_count": len(resets), "required_numbers": list(range(1, protocol.min_resets + 1)),
             "insufficient_cases": insufficient,
             "reason": "each matrix case requires the protocol reset count"},
        )
    failing = [(reset.matrix_case_id, reset.reset_number) for reset in resets if reset.status != "pass"]
    if failing:
        return GateObservation(
            "clean_reset_reproducibility", "fail", None, None,
            {"failed_resets": [f"{case}:{number}" for case, number in failing[:10]]},
        )
    digests = {reset.clean_digest for reset in resets}
    if len(digests) != 1:
        return GateObservation(
            "clean_reset_reproducibility", "fail", None, None,
            {"distinct_clean_digests": len(digests),
             "reason": "reset workspaces did not reconstruct to one identical clean digest"},
        )
    only_digest = next(iter(digests))
    if only_digest != expected_clean_digest:
        return GateObservation(
            "clean_reset_reproducibility", "fail", only_digest, None,
            {"reason": "reset digest does not match the pinned source digest",
             "expected_digest": expected_clean_digest},
        )
    return GateObservation(
        "clean_reset_reproducibility", "pass", only_digest, None,
        {"reset_count": len(resets), "required": protocol.min_resets,
         "case_ids": sorted({reset.matrix_case_id for reset in resets})[:10]},
    )


def _evidence_completeness_gate(
    protocol: AdmissionProtocol, cases: dict[str, list[CaseObservation]], resets: list[ResetObservation],
    finals: dict[str, GateObservation],
) -> GateObservation:
    missing = [case for case in protocol.required_matrix_cases
               if len({item.repetition_index for item in cases.get(case, [])}) < protocol.matrix_repeats]
    undigested = [case.case_id for values in cases.values() for case in values
                  if not _DIGEST_RE.match(case.outcome_digest)]
    missing_gates = [name for name in protocol.gate_names
                     if name != "evidence_completeness" and name not in finals]
    reasons = []
    if missing:
        reasons.append(f"required matrix cases not reported: {', '.join(missing)}")
    if undigested:
        reasons.append(f"cases without outcome digests: {', '.join(sorted(undigested)[:10])}")
    if len(resets) < protocol.min_resets:
        reasons.append(f"only {len(resets)} of {protocol.min_resets} required resets have evidence")
    if missing_gates:
        reasons.append(f"gates with no evidence: {', '.join(missing_gates)}")
    if reasons:
        return GateObservation("evidence_completeness", "fail", None, None, {"reasons": reasons[:10]})
    return GateObservation(
        "evidence_completeness", "pass",
        evidence_digest({
            "cases": {f"{case.case_id}:{case.repetition_index}": case.outcome_digest
                      for values in cases.values() for case in values},
            "reset_count": len(resets),
        }),
        None,
        {"case_count": len(cases), "reset_count": len(resets), "gate_count": len(finals)},
    )


def classify(
    session: Session, *, run: TaskAdmissionRunRow, protocol: AdmissionProtocol,
    outcome: ExecutorOutcome | None,
) -> tuple[dict[str, GateObservation], list[ResetObservation]]:
    """Produce the final observation for every protocol gate.

    A gate the executor did not report is left to the caller as MISSING (it is
    simply absent from the returned mapping) - a missing mandatory gate blocks
    admission rather than being silently treated as a pass.
    """
    revision = session.get(TaskRevisionRow, run.task_revision_id)
    if revision is None:
        raise conflict("the admission run references a task revision that no longer exists")

    finals: dict[str, GateObservation] = {}
    finals["manifest_schema"] = _manifest_schema_gate(session, revision, protocol)
    resets: list[ResetObservation] = list(outcome.resets) if outcome is not None else []
    cases: dict[str, list[CaseObservation]] = {}
    if outcome is not None:
        for case in outcome.cases:
            cases.setdefault(case.case_id, []).append(case)

    if outcome is None:
        # No behavioral evidence at all: every matrix-derived gate fails as
        # missing evidence (recorded with a reason), and executor gates stay
        # absent so they surface as not_run on the stored rows.
        reason = {"reason": "no executor outcome was collected for this run"}
        for gate in ("baseline_behavior", "reference_behavior", "independent_alternative",
                     "shortcut_adversarial_controls", "clean_reset_reproducibility"):
            finals[gate] = GateObservation(gate, "fail", None, None, reason)
    else:
        finals["baseline_behavior"] = _case_gate(
            "baseline", cases, expect_pass=False, required_repeats=protocol.matrix_repeats)
        finals["reference_behavior"] = _case_gate(
            "reference", cases, expect_pass=True, required_repeats=protocol.matrix_repeats)
        finals["independent_alternative"] = _case_gate(
            "alternative", cases, expect_pass=True, required_repeats=protocol.matrix_repeats)
        finals["shortcut_adversarial_controls"] = _shortcut_gate(cases, protocol.required_matrix_cases)
        finals["clean_reset_reproducibility"] = _clean_reset_gate(
            resets, protocol, expected_clean_digest=run.source_digest,
            matrix_case_ids=set(cases),
        )
        for observation in outcome.gates:
            if observation.gate_name in protocol.gate_names and observation.gate_name not in finals:
                finals[observation.gate_name] = observation

    finals["evidence_completeness"] = _evidence_completeness_gate(protocol, cases, resets, finals)
    return finals, resets


# ---------------------------------------------------------------------------
# Finalization: claim the run terminal, write immutable evidence, transition.
# ---------------------------------------------------------------------------


def finalize_run(
    session: Session, *, run: TaskAdmissionRunRow, protocol: AdmissionProtocol,
    outcome: ExecutorOutcome | None, failure_reason: str | None,
    actor_user_id: UUID | None, now: datetime | None = None,
) -> str | None:
    """Finalize a `running` run. Returns the terminal status, or None when a
    concurrent cancel already claimed the run (this caller then writes nothing).

    A run passes only when EVERY mandatory gate exists and passes.
    """
    now = now or datetime.now(timezone.utc)
    finals, resets = classify(session, run=run, protocol=protocol, outcome=outcome)

    required_missing = [name for name in protocol.required_gate_names if name not in finals]
    required_failed = sorted(
        name for name in protocol.required_gate_names
        if name in finals and finals[name].status != "pass"
    )
    optional_failed = sorted(
        name for name in protocol.gate_names
        if not next(spec.required for spec in protocol.gates if spec.name == name)
        and name in finals and finals[name].status != "pass"
    )
    passed = not required_missing and not required_failed

    reasons: list[str] = []
    if failure_reason:
        reasons.append(failure_reason)
    if required_missing:
        reasons.append("mandatory gate(s) never executed: " + ", ".join(required_missing))
    if required_failed:
        reasons.append("mandatory gate(s) failed: " + ", ".join(required_failed))
    if optional_failed:
        reasons.append("optional gate(s) not passing: " + ", ".join(optional_failed))
    if passed and optional_failed:
        passed = False  # an optional gate that ran and failed is still disclosed; required-only pass stands
    result_digest = evidence_digest({
        "schema_version": "aieb.admission-run-result/v1",
        "revision_digest": run.revision_digest,
        "protocol_digest": run.protocol_digest,
        "gates": {name: {"status": observation.status, "observed_digest": observation.observed_digest}
                  for name, observation in sorted(finals.items())},
        "missing_gates": sorted(required_missing),
        "resets": [{"case": reset.matrix_case_id, "n": reset.reset_number,
                    "clean_digest": reset.clean_digest, "status": reset.status}
                   for reset in resets],
    })

    # Hold the parent row lock while writing its evidence.  Reset inserts are
    # database-guarded to a RUNNING parent, then the run is made terminal only
    # after all evidence is durable in this same transaction.  A concurrent
    # cancel waits for this lock and cannot split terminal state from evidence.
    claimed = session.execute(
        select(TaskAdmissionRunRow).where(TaskAdmissionRunRow.id == run.id).with_for_update()
    ).scalar_one_or_none()
    if claimed is None or claimed.status != "running":
        return None

    for name, observation in finals.items():
        row = session.execute(
            select(TaskAdmissionGateRow).where(
                TaskAdmissionGateRow.admission_run_id == run.id,
                TaskAdmissionGateRow.gate_name == name,
            )
        ).scalar_one_or_none()
        if row is None:
            continue  # gate rows are created with the run; absence would already fail evidence completeness
        if row.status != "not_run":
            continue
        row.status = observation.status if observation.status in ADMISSION_GATE_STATUSES else "indeterminate"
        row.observed_digest = observation.observed_digest
        row.evidence_reference = observation.evidence_reference
        row.details = _bounded_json(observation.details)
        row.completed_at = now

    seen_resets: set[tuple[str, int]] = set()
    for reset in resets:
        key = (reset.matrix_case_id, reset.reset_number)
        if key in seen_resets:
            continue
        seen_resets.add(key)
        session.add(TaskAdmissionResetRow(
            admission_run_id=run.id,
            matrix_case_id=reset.matrix_case_id[:64],
            reset_number=reset.reset_number,
            clean_digest=reset.clean_digest,
            status=reset.status,
            evidence_reference=_bounded_text(reset.evidence_reference, 500),
        ))

    session.flush()
    claimed.status = "passed" if passed else "failed"
    claimed.result_digest = result_digest
    claimed.failure_reason = None if passed else (
        "; ".join(reasons)[:2000] or "admission gates did not all pass"
    )
    claimed.completed_at = now

    state = admission_state(session, run.task_revision_id)
    if state is not None:
        state.status = "pending_independent_review" if passed else "admission_failed"
        state.failure_reason = None if passed else (
            claimed.failure_reason or "admission gates did not all pass"
        )
    session.flush()
    return "passed" if passed else "failed"


def cancel_run(session: Session, *, run: TaskAdmissionRunRow, actor_user_id: UUID, reason: str) -> str:
    """Cancel a pending/running run. The run claim happens BEFORE the state
    transition so a concurrent finalize cannot interleave."""
    now = datetime.now(timezone.utc)
    claimed = session.execute(
        update(TaskAdmissionRunRow)
        .where(TaskAdmissionRunRow.id == run.id, TaskAdmissionRunRow.status.in_(("pending", "running")))
        .values(status="cancelled", failure_reason=f"cancelled: {reason[:500]}", completed_at=now)
        .returning(TaskAdmissionRunRow)
    ).scalar_one_or_none()
    if claimed is None:
        raise conflict(f"admission run is {run.status}; only a pending or running run can be cancelled")
    state = admission_state(session, run.task_revision_id)
    if state is not None and state.status in ("admission_pending", "admission_running"):
        state.status = "admission_failed"
        state.failure_reason = claimed.failure_reason
    session.flush()
    return "cancelled"


# ---------------------------------------------------------------------------
# Independent review: the only path to `admitted`.
# ---------------------------------------------------------------------------


def record_review(
    session: Session, *, run: TaskAdmissionRunRow, reviewer_id: UUID, decision: str,
    scope: str, evidence_digest_value: str, independence_declaration: bool,
    reason: str,
) -> TaskAdmissionReviewRow:
    if decision not in ("approve", "reject"):
        raise invalid_request("review decision must be approve or reject")
    if not independence_declaration:
        raise invalid_request("an independent review requires an explicit independence declaration")
    if evidence_digest_value != (run.result_digest or ""):
        raise invalid_request(
            "evidence_digest must be the admission run's result_digest; the review must bind "
            "to the exact evidence it decided on"
        )
    if run.status != "passed":
        raise conflict(f"only a passed admission run can be reviewed, not one in status {run.status}")
    state = admission_state(session, run.task_revision_id)
    if state is None or state.status != "pending_independent_review":
        found = state.status if state is not None else "missing"
        raise conflict(f"task revision admission state is {found}; expected pending_independent_review")
    if state.author_user_id is None:
        # Fail closed: independence cannot be asserted against an unknown author.
        raise conflict("the task revision has no recorded author; independent review cannot be established")
    if reviewer_id == state.author_user_id:
        raise conflict("the task author cannot review their own admission")
    if reviewer_id == run.requested_by_user_id:
        raise conflict("the admission requester cannot review their own admission")
    if session.execute(
        select(TaskAdmissionReviewRow.id).where(TaskAdmissionReviewRow.admission_run_id == run.id)
    ).scalar_one_or_none() is not None:
        raise conflict("this admission run has already been reviewed")

    review = TaskAdmissionReviewRow(
        task_revision_id=run.task_revision_id,
        admission_run_id=run.id,
        reviewer_user_id=reviewer_id,
        author_user_id=state.author_user_id,
        requested_by_user_id=run.requested_by_user_id,
        decision=decision,
        scope=scope,
        evidence_digest=evidence_digest_value,
        independence_declaration=True,
        reason=reason,
    )
    session.add(review)
    session.flush()
    state.status = "admitted" if decision == "approve" else "rejected"
    state.failure_reason = None if decision == "approve" else f"rejected by independent review: {reason[:500]}"
    session.flush()
    return review


# ---------------------------------------------------------------------------
# Release eligibility: only admitted revisions may be planned or published.
# ---------------------------------------------------------------------------


def release_eligibility_error(session: Session, revision_ids: list[UUID]) -> str | None:
    """Eligible task revision =
        state == admitted
        AND latest admission run status == passed
        AND every required gate still passes with stored evidence
        AND an approving independent review exists for that run
        AND the admitted manifest/source/evaluator digests still match.

    Returns a bounded human-readable reason, or None when every revision is
    eligible. Fail closed on anything unverifiable.
    """
    if not revision_ids:
        return None
    problems: list[str] = []
    revisions = session.execute(
        select(TaskRevisionRow).where(TaskRevisionRow.id.in_(revision_ids))
    ).scalars().all()
    by_id = {revision.id: revision for revision in revisions}
    for revision_id in revision_ids:
        revision = by_id.get(revision_id)
        if revision is None:
            problems.append(f"revision {revision_id} does not exist")
            continue
        label = f"{revision.slug} {revision.version}"
        state = admission_state(session, revision.id)
        if state is None:
            problems.append(f"{label} has no admission record")
            continue
        if state.status != "admitted":
            problems.append(f"{label} is {state.status}, not admitted")
            continue
        run = latest_run(session, revision.id)
        if run is None:
            problems.append(f"{label} has no admission run")
            continue
        if run.status != "passed":
            problems.append(f"{label} has no passing admission run (latest run is {run.status})")
            continue
        protocol = get_protocol(run.protocol_version)
        if protocol is None:
            problems.append(f"{label} was admitted under an unknown protocol {run.protocol_version}")
            continue
        if run.protocol_digest != protocol.digest():
            problems.append(f"{label} admission protocol digest does not match version {run.protocol_version}")
            continue
        if run.revision_digest != revision.revision_digest:
            problems.append(f"{label} admitted revision digest no longer matches the stored revision")
            continue
        if run.manifest_digest != revision.manifest_digest:
            problems.append(f"{label} admitted manifest digest no longer matches the stored manifest")
            continue
        if run.source_digest != revision.source_digest:
            problems.append(f"{label} admitted source digest no longer matches the stored source identity")
            continue
        if run.evaluator_digest != _evaluator_digest(session, revision):
            problems.append(f"{label} admitted evaluator digest no longer matches the stored evaluator")
            continue
        gates = session.execute(
            select(TaskAdmissionGateRow).where(TaskAdmissionGateRow.admission_run_id == run.id)
        ).scalars().all()
        by_name = {gate.gate_name: gate for gate in gates}
        failing = [
            name for name in protocol.required_gate_names
            if name not in by_name or by_name[name].status != "pass"
        ]
        if failing:
            problems.append(f"{label} admission gates are not passing: {', '.join(failing[:5])}")
            continue
        review = session.execute(
            select(TaskAdmissionReviewRow).where(
                TaskAdmissionReviewRow.admission_run_id == run.id,
                TaskAdmissionReviewRow.decision == "approve",
            )
        ).scalar_one_or_none()
        if review is None:
            problems.append(f"{label} has no approving independent review for its passing run")
            continue
        reset_rows = session.execute(
            select(TaskAdmissionResetRow).where(
                TaskAdmissionResetRow.admission_run_id == run.id,
                TaskAdmissionResetRow.status == "pass",
            )
        ).scalars().all()
        required_numbers = set(range(1, protocol.min_resets + 1))
        reset_numbers_by_case: dict[str, set[int]] = {}
        for reset in reset_rows:
            reset_numbers_by_case.setdefault(reset.matrix_case_id, set()).add(reset.reset_number)
        missing_reset_cases = {
            case_id: sorted(required_numbers - reset_numbers_by_case.get(case_id, set()))
            for case_id in protocol.required_matrix_cases
            if not required_numbers.issubset(reset_numbers_by_case.get(case_id, set()))
        }
        if missing_reset_cases:
            problems.append(
                f"{label} lacks required passing clean resets by matrix case: {missing_reset_cases}"
            )
    if not problems:
        return None
    shown = "; ".join(problems[:5])
    if len(problems) > 5:
        shown += f"; and {len(problems) - 5} more"
    return f"task revisions are not release-eligible: {shown}"


def _passing_reset_count(session: Session, run_id: UUID) -> int:
    from sqlalchemy import func

    return session.execute(
        select(func.count()).select_from(TaskAdmissionResetRow).where(
            TaskAdmissionResetRow.admission_run_id == run_id,
            TaskAdmissionResetRow.status == "pass",
        )
    ).scalar_one()


def require_release_eligible(session: Session, revision_ids: list[UUID]) -> None:
    error = release_eligibility_error(session, revision_ids)
    if error is not None:
        raise conflict(error)


# ---------------------------------------------------------------------------
# Fixture-only admission seeding.
#
# TEST/SEED FIXTURES ONLY - never called from a route. It writes structurally
# valid admitted state (walking the real state-machine transitions) so that
# campaign/publication tests have an eligible revision to plan with. It is NOT
# evidence of real execution or of genuine independent human review, and no
# official claim may rest on rows it writes.
# ---------------------------------------------------------------------------


def seed_fixture_admission(
    session: Session, revision: TaskRevisionRow, *, author_subject: str = "fixture-author",
    reviewer_subject: str = "fixture-reviewer",
) -> TaskAdmissionReviewRow:
    """Create a structurally complete admitted state for a task revision.

    Fixture-only. Real admission requires `POST .../admissions`, `POST
    .../execute` against an executor, and an independent reviewer deciding
    through `POST .../review`.
    """
    protocol = ADMISSION_PROTOCOLS[CURRENT_PROTOCOL_VERSION]
    author = _fixture_user(session, author_subject)
    requester = author
    reviewer = _fixture_user(session, reviewer_subject)
    if reviewer.id == author.id:
        raise conflict("fixture reviewer must differ from fixture author")

    state = admission_state(session, revision.id)
    if state is None:
        state = TaskAdmissionStateRow(task_revision_id=revision.id, status="frozen", author_user_id=author.id)
        session.add(state)
        session.flush()
    elif state.author_user_id is None:
        raise conflict("fixture admission cannot establish independence for a revision with unknown authorship")

    # Previously this constructed `TaskAdmissionRunRow` by hand instead of
    # calling `start_run()` (bug found 2026-09-22, first exercised against
    # real PostgreSQL): `start_run()` also creates one `not_run`
    # `TaskAdmissionGateRow` per protocol gate, and `finalize_run()` below
    # only ever UPDATES an existing gate row for a name - it silently skips
    # writing a gate whose placeholder row is missing (`if row is None:
    # continue`), on the documented assumption that `start_run()` always
    # created it first. Hand-building the run bypassed that, so every gate
    # silently stayed unwritten and `release_eligibility_error()` later saw
    # zero gate rows for an otherwise "passed" run.
    if state.status != "frozen":
        state.status = "frozen"
    run = start_run(session, revision=revision, requester_id=requester.id, protocol=protocol)
    now = datetime.now(timezone.utc)
    state.status = "admission_running"
    session.flush()
    claimed = claim_for_execution(session, run.id)
    if claimed is None:
        # Never observed until this fixture was actually exercised against
        # real PostgreSQL (2026-09-22): `finalize_run` requires
        # `run.status == "running"` and silently returns None otherwise, but
        # this function used to leave the freshly-created run at its default
        # `pending` status - `finalize_run` below would then always return
        # None and every caller would see a misleading "could not reach a
        # passing run: None". Nothing else can be racing a run this function
        # itself just created and flushed, so a `None` here is a genuine
        # constructed-row bug, not the ordinary losing-concurrent-claim case.
        raise conflict("fixture admission run could not be claimed for execution immediately after creation")
    run = claimed

    resets: list[ResetObservation] = [
        ResetObservation(
            matrix_case_id=case_id,
            reset_number=index + 1,
            clean_digest=revision.source_digest,
            status="pass",
            evidence_reference=f"fixture://admission/{run.id}/reset/{case_id}/{index + 1}",
        )
        for case_id in ("baseline", "reference", "alternative", "shortcut")
        for index in range(protocol.min_resets)
    ]
    cases = tuple(
        CaseObservation(
            case_id=name,
            repetition_index=repetition,
            passed=expected,
            outcome_digest=evidence_digest({"case": name, "repetition": repetition, "fixture": True}),
        )
        for name, expected in (("baseline", False), ("reference", True),
                               ("alternative", True), ("shortcut", False))
        for repetition in range(protocol.matrix_repeats)
    )
    outcome = ExecutorOutcome(
        gates=tuple(
            GateObservation(name, "pass", revision.manifest_digest, f"fixture://admission/{run.id}/{name}",
                            {"fixture": "synthetic admission fixture; no execution performed"})
            for name in _EXECUTOR_GATE_NAMES
        ),
        cases=cases,
        resets=tuple(resets),
    )
    status = finalize_run(
        session, run=run, protocol=protocol, outcome=outcome,
        failure_reason=None, actor_user_id=requester.id, now=now,
    )
    if status != "passed":
        raise conflict(f"fixture admission could not reach a passing run: {status}")
    return record_review(
        session, run=run, reviewer_id=reviewer.id, decision="approve",
        scope="task-admission", evidence_digest_value=run.result_digest or "",
        independence_declaration=True,
        reason="fixture admission: synthetic approval recorded for local tests and seeds only",
    )


def _fixture_user(session: Session, subject: str) -> User:
    user = session.execute(
        select(User).where(User.oidc_issuer == "fixture", User.oidc_subject == subject)
    ).scalar_one_or_none()
    if user is None:
        user = User(oidc_issuer="fixture", oidc_subject=subject, display_name=f"Fixture {subject}")
        session.add(user)
        session.flush()
    return user
