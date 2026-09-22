"""V2-GAP-004 section 6 orchestration adapter: frozen cell -> pinned Harbor run.

This module is the ONLY place a leased cell is translated into a Harbor
`ExecutionSpec`. It exists so the translation is explicit, digest-checked, and
testable in isolation (the smoke tests drive it with a synthetic local
launcher - no paid provider, no real Harbor cluster).

Contract (plan section 6):

- receives ONLY the frozen cell payload: the payload is derived from the
  campaign's FROZEN release manifest (aieb.campaign/v1, `frozen: true`), never
  from a mutable draft or a "latest" reference, and every digest binding
  (trial -> task/entrant identity) is re-verified while building it;
- launches the pinned Harbor/task environment with the pinned task, entrant,
  protocol, and resource settings (CPU/memory/wall-clock deadline all come
  from the frozen manifest - nothing is defaulted from ambient state);
- records requested/reported model identity: the requested model is pinned
  into the spec and the reported model comes back on the outcome so the caller
  can persist both (the existing attempt-model-identity path stores them with
  a coverage label - a disclosed mismatch is evidence, a SPEC that would launch
  a different model than the frozen cell is a wiring bug and raises here);
- captures bounded usage/trace inputs and returns candidate/artifact
  references on the outcome;
- NEVER silently falls back: an unknown `AIEB_DISPATCH_BACKEND` raises instead
  of guessing; selecting `harbor` before V2-GAP-001's leased-worker dispatch is
  wired refuses loudly (worker boot fails) instead of quietly running the
  local runner bridge; spec/payload pinning drift raises instead of launching
  the drifted spec.

Backend selection is explicit configuration, not inference:

- ``AIEB_DISPATCH_BACKEND`` unset/empty -> ``local`` (the declared development
  path; disclosed, not hidden);
- ``AIEB_DISPATCH_BACKEND=harbor`` -> the explicit Harbor path used by the
  leased worker for engineering cells; verification remains the AIEB replay
  lease over the retained candidate;
- anything else -> `DispatchConfigurationError`.
"""

from __future__ import annotations

import asyncio
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol

from aieb_core.models import EntrantRevision, ResolvedCampaign, TaskRevision, Trial as TrialContract

DISPATCH_BACKENDS = ("local", "harbor")


class DispatchConfigurationError(RuntimeError):
    """The dispatch backend configuration cannot be honored as written."""


class DispatchIntegrityError(RuntimeError):
    """A frozen cell would be dispatched as something other than what the
    frozen manifest pins (task, entrant, model, protocol, or resources)."""


def dispatch_backend_from_env(env: Mapping[str, str] | None = None) -> str:
    """Resolve the explicit dispatch-backend configuration. Unset means the
    declared `local` path; an unrecognized value raises rather than silently
    selecting a backend."""
    source = os.environ if env is None else env
    raw = str(source.get("AIEB_DISPATCH_BACKEND") or "").strip()
    if not raw:
        return "local"
    normalized = raw.lower()
    if normalized not in DISPATCH_BACKENDS:
        raise DispatchConfigurationError(
            f"unknown AIEB_DISPATCH_BACKEND {raw!r}; expected one of {', '.join(DISPATCH_BACKENDS)}. "
            "Refusing to guess a dispatch backend."
        )
    return normalized


def refuse_unwired_harbor_dispatch(backend: str) -> None:
    """Compatibility check retained for older callers.

    Harbor is now wired by the hosted worker. Unknown values are still refused
    before any work is claimed.
    """
    if backend not in DISPATCH_BACKENDS:
        raise DispatchConfigurationError(f"unsupported dispatch backend: {backend!r}")


@dataclass(frozen=True)
class FrozenCellPayload:
    """Everything one cell execution is allowed to know, all pinned by the
    frozen release manifest. No field may be filled from mutable state."""

    campaign_id: str
    manifest_digest: str
    cohort_digest: str | None
    trial_id: str
    cell_digest: str
    task_id: str
    task_version: str
    task_digest: str
    entrant_id: str
    entrant_version: str
    entrant_digest: str
    track: str
    requested_model: str | None
    model_settings_digest: str | None
    protocol_id: str
    scoring_digest: str
    max_replacements: int
    repetition_index: int
    order_index: int
    attempt_number: int
    engineer_wall_seconds: int
    verification_wall_seconds: int
    engineer_cpu: int
    engineer_memory_mb: int

    @property
    def deadline_seconds(self) -> float:
        """The frozen per-cell wall-clock deadline budget (plan section 5)."""
        return float(self.engineer_wall_seconds + self.verification_wall_seconds)


def build_frozen_cell_payload(
    resolved: Mapping[str, Any], *,
    trial_id: str,
    attempt_number: int,
    campaign_id: str | None = None,
    manifest_digest: str | None = None,
) -> FrozenCellPayload:
    """Derive the frozen cell payload from an aieb.campaign/v1 release
    manifest, re-verifying every digest binding along the way.

    Raises DispatchIntegrityError for anything not explicitly pinned: a
    non-frozen manifest, an unknown trial id, a trial whose task/entrant
    digests do not match the very revisions the manifest lists (i.e. a
    manifest that does not even agree with itself), or missing protocol/
    budget/resource pins."""
    if resolved.get("schema_version") != "aieb.campaign/v1" or resolved.get("frozen") is not True:
        raise DispatchIntegrityError("dispatch requires a frozen aieb.campaign/v1 release manifest")
    if not manifest_digest:
        raise DispatchIntegrityError("dispatch requires the frozen campaign manifest digest")
    try:
        canonical_manifest_digest = ResolvedCampaign.model_validate(resolved).digest()
    except Exception as exc:  # noqa: BLE001 - malformed frozen input is integrity evidence
        raise DispatchIntegrityError("frozen campaign manifest failed canonical validation") from exc
    if manifest_digest != canonical_manifest_digest:
        raise DispatchIntegrityError(
            "supplied campaign manifest digest does not match the canonical frozen manifest"
        )
    trials = resolved.get("trials")
    if not isinstance(trials, list):
        raise DispatchIntegrityError("frozen manifest has no trial matrix")
    trial = next((t for t in trials if isinstance(t, dict) and str(t.get("id")) == str(trial_id)), None)
    if trial is None:
        raise DispatchIntegrityError(f"frozen manifest does not contain cell {trial_id}")

    tasks = {TaskRevision.model_validate(t).digest(): t for t in resolved.get("tasks", []) if isinstance(t, dict)}
    entrants = {EntrantRevision.model_validate(e).digest(): e for e in resolved.get("entrants", []) if isinstance(e, dict)}
    task = tasks.get(trial.get("task_digest"))
    entrant = entrants.get(trial.get("entrant_digest"))
    if task is None or entrant is None:
        raise DispatchIntegrityError(
            "frozen manifest cell references a task/entrant digest that does not match any revision "
            "it declares; the manifest does not agree with itself and must not be dispatched"
        )

    protocol = resolved.get("protocol")
    budget = resolved.get("budget")
    if not isinstance(protocol, dict) or not isinstance(budget, dict):
        raise DispatchIntegrityError("frozen manifest is missing its protocol or budget profile")
    max_replacements = protocol.get("max_replacements")
    if not isinstance(max_replacements, int):
        raise DispatchIntegrityError("frozen protocol does not pin max_replacements")
    engineer_wall = budget.get("engineer_wall_seconds")
    verification_wall = budget.get("verification_wall_seconds")
    engineer_cpu = budget.get("engineer_cpu")
    engineer_memory = budget.get("engineer_memory_mb")
    if not all(isinstance(v, int) for v in (engineer_wall, verification_wall, engineer_cpu, engineer_memory)):
        raise DispatchIntegrityError("frozen budget does not pin the wall-clock/CPU/memory resource profile")

    engineer_model = entrant.get("engineer_model")
    engineer_model = engineer_model if isinstance(engineer_model, dict) else {}
    requested_model = engineer_model.get("requested_model")
    settings_digest = engineer_model.get("settings_digest")
    cohort = resolved.get("cohort")
    cohort_digest = cohort.get("digest") if isinstance(cohort, dict) else None

    return FrozenCellPayload(
        campaign_id=str(campaign_id or resolved.get("id")),
        manifest_digest=canonical_manifest_digest,
        cohort_digest=cohort_digest,
        trial_id=str(trial["id"]),
        cell_digest=TrialContract.model_validate(trial).digest(),
        task_id=str(task["id"]),
        task_version=str(task["version"]),
        task_digest=str(trial["task_digest"]),
        entrant_id=str(entrant["id"]),
        entrant_version=str(entrant["agent_version"]),
        entrant_digest=str(trial["entrant_digest"]),
        track=str(entrant.get("track") or ""),
        requested_model=str(requested_model) if requested_model else None,
        model_settings_digest=str(settings_digest) if settings_digest else None,
        protocol_id=str(protocol.get("id") or ""),
        scoring_digest=str(protocol.get("scoring_digest") or ""),
        max_replacements=max_replacements,
        repetition_index=int(trial.get("repetition_index", 0)),
        order_index=int(trial.get("order_index", 0)),
        attempt_number=int(attempt_number),
        engineer_wall_seconds=engineer_wall,
        verification_wall_seconds=verification_wall,
        engineer_cpu=engineer_cpu,
        engineer_memory_mb=engineer_memory,
    )


def build_execution_spec(
    payload: FrozenCellPayload, *,
    task_dir: Path,
    runs_dir: Path,
    agent_import_path: str,
    hardened_isolation: bool = False,
) -> Any:
    """Translate a frozen cell into Harbor's ExecutionSpec - pinned task dir,
    deterministic trial name, the frozen wall-clock deadline as the timeout,
    the frozen CPU/memory profile, and the frozen requested model threaded
    into Harbor's AgentConfig.model_name."""
    from aieb_runner.backends.base import ExecutionSpec, IsolationPolicy

    spec = ExecutionSpec(
        task_dir=task_dir,
        runs_dir=runs_dir,
        trial_name=f"cell-{payload.cell_digest[:16]}-r{payload.repetition_index}-a{payload.attempt_number}",
        agent_import_path=agent_import_path,
        agent_timeout_sec=payload.deadline_seconds,
        cpu_limit=payload.engineer_cpu,
        memory_limit_mb=payload.engineer_memory_mb,
        isolation=IsolationPolicy(hardened_isolation_required=hardened_isolation),
        model_name=payload.requested_model,
        manifest_digest=payload.manifest_digest,
        agent_kwargs=(
            {"requested_model": payload.requested_model, "model_settings_digest": payload.model_settings_digest}
            if payload.requested_model else {}
        ),
    )
    verify_spec_pinning(payload, spec)
    return spec


def verify_spec_pinning(payload: FrozenCellPayload, spec: Any) -> None:
    """Refuse a spec that would run something other than the frozen cell.

    This is the adapter's no-silent-substitution guarantee at the last moment
    before launch: a spec whose model, resources, or deadline differ from the
    frozen manifest is a wiring bug, never an optimization."""
    if spec.model_name != payload.requested_model:
        raise DispatchIntegrityError(
            f"spec would launch model {spec.model_name!r} but the frozen cell pins "
            f"{payload.requested_model!r}; refusing to substitute a different model"
        )
    if getattr(spec, "manifest_digest", None) != payload.manifest_digest:
        raise DispatchIntegrityError(
            "spec campaign manifest digest differs from the canonical frozen manifest; refusing to dispatch"
        )
    if spec.cpu_limit != payload.engineer_cpu or spec.memory_limit_mb != payload.engineer_memory_mb:
        raise DispatchIntegrityError(
            "spec resource profile differs from the frozen budget's engineer_cpu/engineer_memory_mb; "
            "refusing to dispatch with unpinned resources"
        )
    if float(spec.agent_timeout_sec) != payload.deadline_seconds:
        raise DispatchIntegrityError(
            f"spec timeout {spec.agent_timeout_sec} differs from the frozen per-cell deadline budget "
            f"{payload.deadline_seconds}; refusing to dispatch outside the frozen deadline"
        )


class CellLauncher(Protocol):
    """The narrow launch surface the adapter needs - implemented by
    `aieb_runner.HarborBackend` and by the synthetic launcher the smoke tests
    use. Injected, never constructed from ambient configuration, so the
    adapter cannot quietly choose its own execution path.

    Matches `aieb_runner.backends.base.ExecutionBackend` exactly (including
    `preflight`) so any launcher can be driven through
    `aieb_runner.backends.orchestration.run_bounded` - see `dispatch_cell`."""

    async def preflight(self) -> Any: ...

    async def launch(self, spec: Any) -> Any: ...

    async def status(self, handle: Any) -> Any: ...

    async def stop(self, handle: Any, reason: str) -> None: ...

    async def collect(self, handle: Any) -> Any: ...

    async def cleanup(self, handle: Any) -> Any: ...


@dataclass(frozen=True)
class DispatchOutcome:
    """What one dispatched cell produced: candidate/artifact references plus
    the identity/usage/trace evidence the caller must persist."""

    payload: FrozenCellPayload
    trial_dir: str | None
    result_path: str | None
    manifest_entries: tuple[dict, ...] = ()
    reported_model: str | None = None
    usage: dict | None = None
    trace: tuple[dict, ...] = field(default_factory=tuple)
    # Deadline attribution (finding #2 closure): true when `run_bounded` had to
    # stop the launch itself because it exceeded the frozen per-cell
    # `deadline_seconds` budget, rather than the launcher finishing on its own.
    # The caller persists this the same way `runner_bridge` persists a local
    # deadline/cancellation attribution - never silently folded into a plain
    # completed outcome.
    deadline_exceeded: bool = False
    terminal_state: str | None = None

    @property
    def requested_model(self) -> str | None:
        return self.payload.requested_model

    @property
    def identity_record(self) -> dict:
        """The requested/reported model identity pair the caller persists via
        the existing attempt-model-identity path. A reported value different
        from the requested one is DISCLOSED here (and labeled by that path) -
        it is never dropped, and the spec-level substitution it would imply
        already raised at pinning time."""
        return {
            "requested_model": self.payload.requested_model,
            "reported_model": self.reported_model,
        }


async def dispatch_cell(
    payload: FrozenCellPayload,
    *,
    launcher: CellLauncher,
    task_dir: Path,
    runs_dir: Path,
    agent_import_path: str,
    hardened_isolation: bool = False,
    poll_seconds: float = 0.25,
    timeout_grace_seconds: float = 10.0,
    cancel_event: threading.Event | None = None,
) -> DispatchOutcome:
    """Launch ONE frozen cell through the injected launcher and collect its
    artifacts. The spec is built (and pinning-verified) inside; the launcher
    receives nothing but the frozen spec; the outcome returns artifact
    references plus requested/reported identity, bounded usage, and trace.

    Runtime deadline enforcement (finding #2 closure): this previously called
    `launcher.launch()` then immediately `launcher.collect()`, never using
    `status()`/`stop()`, so `payload.deadline_seconds` was encoded into the
    spec but never actually enforced against a launcher that keeps running
    past it. This now polls `status()` and calls `stop()` at the frozen
    deadline (mirroring `aieb_runner.backends.orchestration.run_bounded`,
    which the local backend already uses), so the attempt is stopped, not
    just timed-out-on-paper. `deadline_exceeded` on the returned outcome
    records which path was taken so the caller can attribute the terminal
    status correctly, the same way `runner_bridge` attributes a local
    cancellation/deadline outcome."""
    from aieb_runner.backends.base import ExecutionState

    spec = build_execution_spec(
        payload,
        task_dir=task_dir, runs_dir=runs_dir, agent_import_path=agent_import_path,
        hardened_isolation=hardened_isolation,
    )
    capability = await launcher.preflight()
    if not getattr(capability, "ready", True):
        raise DispatchIntegrityError(f"launcher preflight failed for cell {payload.trial_id}: {capability}")

    handle = await launcher.launch(spec)
    deadline_exceeded = False
    try:
        deadline = asyncio.get_running_loop().time() + spec.agent_timeout_sec
        status = await launcher.status(handle)
        while status.state == ExecutionState.RUNNING:
            if cancel_event is not None and cancel_event.is_set():
                await launcher.stop(handle, "campaign cancellation requested")
                status = await launcher.status(handle)
                if status.state == ExecutionState.RUNNING:
                    raise DispatchIntegrityError(
                        f"launcher for cell {payload.trial_id} kept running after cancellation"
                    )
                break
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                deadline_exceeded = True
                await launcher.stop(handle, "frozen cell deadline exceeded")
                grace_deadline = asyncio.get_running_loop().time() + timeout_grace_seconds
                status = await launcher.status(handle)
                while status.state == ExecutionState.RUNNING and asyncio.get_running_loop().time() < grace_deadline:
                    await asyncio.sleep(min(poll_seconds, max(0.01, grace_deadline - asyncio.get_running_loop().time())))
                    status = await launcher.status(handle)
                if status.state == ExecutionState.RUNNING:
                    raise DispatchIntegrityError(
                        f"launcher for cell {payload.trial_id} kept running after a deadline stop was issued"
                    )
                break
            await asyncio.sleep(min(poll_seconds, remaining))
            status = await launcher.status(handle)
        artifacts = await launcher.collect(handle)
    finally:
        try:
            await launcher.cleanup(handle)
        except Exception:  # noqa: BLE001 - cleanup failure must not mask the dispatch result
            pass
    manifest = getattr(artifacts, "manifest", ()) or ()
    manifest = tuple(entry for entry in manifest if isinstance(entry, dict))
    reported = None
    for entry in manifest:
        if isinstance(entry.get("reported_model"), str):
            reported = entry["reported_model"]
            break
    usage = next((entry.get("usage") for entry in manifest if isinstance(entry.get("usage"), dict)), None)
    trace = tuple(entry.get("events") for entry in manifest if isinstance(entry.get("events"), list))
    trace = tuple(event for events in trace for event in events if isinstance(event, dict))
    trial_dir = getattr(artifacts, "trial_dir", None)
    result_path = getattr(artifacts, "result_path", None)
    return DispatchOutcome(
        payload=payload,
        trial_dir=str(trial_dir) if trial_dir is not None else None,
        result_path=str(result_path) if result_path is not None else None,
        manifest_entries=manifest,
        reported_model=reported,
        usage=usage,
        trace=trace,
        deadline_exceeded=deadline_exceeded,
        terminal_state=str(status.state),
    )
