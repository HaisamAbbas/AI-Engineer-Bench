"""Integrity digests for persisted evaluation and publication evidence."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def evidence_digest(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def task_revision_digest(manifest: dict[str, Any], ticket_text: str | None) -> str:
    return evidence_digest({
        "schema_version": "aieb.task-revision-identity/v1",
        "manifest": manifest,
        "ticket_text": ticket_text,
    })


def evaluation_digest(
    *, candidate_id: Any, evaluator_id: Any, fixture_id: Any, schedule_digest: str,
    verdict: str | None, result: dict[str, Any] | None,
) -> str:
    return evidence_digest({
        "candidate_id": str(candidate_id),
        "evaluator_id": str(evaluator_id),
        "fixture_id": str(fixture_id),
        "schedule_digest": schedule_digest,
        "verdict": verdict,
        "result": result,
    })


def attempt_trace_digest(events: list[dict[str, Any]]) -> str:
    return evidence_digest(events)
