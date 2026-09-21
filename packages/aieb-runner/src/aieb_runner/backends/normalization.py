"""Normalize Harbor output into AIEB candidate/evidence primitives."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from aieb_core.models import SubmissionPolicy

from ..artifacts import ArtifactStore, StoredCandidate, collect_candidate
from .base import CandidateArtifacts, ExecutionSpec
from .orchestration import BackendRunResult, ExecutionBackend, run_bounded


class HarborResultError(ValueError):
    """Harbor produced an invalid or incomplete result envelope."""


@dataclass(frozen=True)
class NormalizedHarborResult:
    candidate: StoredCandidate
    reward: float | None
    verifier_result: dict[str, object]
    raw_result: dict[str, object]


async def execute_and_normalize(
    backend: ExecutionBackend,
    spec: ExecutionSpec,
    *,
    frozen_source: Path,
    submission: SubmissionPolicy,
    base_revision_digest: str,
    store: ArtifactStore,
    access_scope: str,
    poll_seconds: float = 0.25,
    timeout_grace_seconds: float = 10.0,
) -> tuple[BackendRunResult, NormalizedHarborResult]:
    """Execute a bounded Harbor run, then normalize only its collected artifacts.

    This is the worker-facing seam: backend lifecycle failures and candidate
    contract failures remain distinct, and normalization happens only after the
    backend has completed its mandatory cleanup.
    """
    run = await run_bounded(
        backend,
        spec,
        poll_seconds=poll_seconds,
        timeout_grace_seconds=timeout_grace_seconds,
    )
    normalized = normalize_harbor_result(
        artifacts=run.artifacts,
        frozen_source=frozen_source,
        submission=submission,
        base_revision_digest=base_revision_digest,
        store=store,
        access_scope=access_scope,
    )
    return run, normalized


def _read_result(artifacts: CandidateArtifacts) -> dict[str, object]:
    try:
        value = json.loads(artifacts.result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HarborResultError("Harbor result.json is unreadable") from exc
    if not isinstance(value, dict):
        raise HarborResultError("Harbor result.json must be an object")
    verifier = value.get("verifier_result")
    if verifier is not None and not isinstance(verifier, dict):
        raise HarborResultError("Harbor verifier_result must be an object")
    return value


def _reward(result: dict[str, object]) -> float | None:
    verifier = result.get("verifier_result")
    if not isinstance(verifier, dict):
        return None
    rewards = verifier.get("rewards")
    if not isinstance(rewards, dict):
        return None
    value = rewards.get("reward")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HarborResultError("Harbor reward must be numeric")
    return float(value)


def normalize_harbor_result(
    *,
    artifacts: CandidateArtifacts,
    frozen_source: Path,
    submission: SubmissionPolicy,
    base_revision_digest: str,
    store: ArtifactStore,
    access_scope: str,
) -> NormalizedHarborResult:
    """Build an AIEB StoredCandidate from a Harbor candidate artifact."""

    result = _read_result(artifacts)
    candidate_root = artifacts.trial_dir / "artifacts" / "candidate"
    if not candidate_root.is_dir():
        raise HarborResultError("Harbor candidate artifact directory is missing")
    with TemporaryDirectory(prefix="aieb-harbor-normalize-") as temporary:
        workspace = Path(temporary) / "workspace"
        shutil.copytree(frozen_source, workspace)
        for source in candidate_root.rglob("*"):
            relative = source.relative_to(candidate_root)
            destination = workspace / relative
            if source.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
            elif source.is_file():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
            else:
                raise HarborResultError(f"unsupported Harbor artifact entry: {relative}")
        candidate = collect_candidate(
            frozen_source=frozen_source,
            workspace=workspace,
            submission=submission,
            base_revision_digest=base_revision_digest,
            store=store,
            access_scope=access_scope,
        )
    verifier = result.get("verifier_result")
    return NormalizedHarborResult(
        candidate=candidate,
        reward=_reward(result),
        verifier_result=verifier if isinstance(verifier, dict) else {},
        raw_result=result,
    )
