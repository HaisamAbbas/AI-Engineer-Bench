"""Golden and negative vectors for the strict v2 benchmark contracts."""

from __future__ import annotations

import unittest
from uuid import UUID

from aieb_core.contracts_v2 import (
    CampaignCell, PublicationSnapshot, TrackAProtocol, TrackBProtocol,
    V2Campaign, V2NetworkPolicy, V2ResourceLimits, V2Source, V2TaskRevision,
)
from aieb_core.models import Track


DIGEST = "a" * 64


def task_payload(**overrides: object) -> dict:
    value: dict = {
        "schema_version": "aieb.task/v2", "id": "rag-freshness", "version": "2.0.0",
        "family_id": "rag", "difficulty": "standard", "track": "agents",
        "dependency_mode": "fixture",
        "source": {"repository_digest": DIGEST, "revision": "abc1234", "license_id": "Apache-2.0", "provenance_digest": DIGEST, "overlap_review_ref": "review-1"},
        "license_id": "Apache-2.0", "provenance_digest": DIGEST, "harbor_task_digest": DIGEST,
        "official_image": "python:3.12@sha256:" + DIGEST,
        "network_policy": {"mode": "none", "allowed_hosts": []},
        "resources": {"cpu": 2, "memory_mb": 2048, "wall_seconds": 300, "max_output_bytes": 100000},
        "public_status": "public",
        "evaluator": {"schema_version": "aieb.evaluator/v2", "id": "rag-evaluator", "revision": "1", "evaluator_digest": DIGEST, "public_fixture_ref": "fixtures/public", "hidden_fixture_ref": "holdout://rag/1", "protocol_version": "protocol-a"},
        "protocol_version": "protocol-a", "allowed_paths": ["src/**", "README.md"],
    }
    value.update(overrides)
    return value


class V2ContractsTest(unittest.TestCase):
    def test_task_golden_vector_is_canonical_and_digestable(self) -> None:
        task = V2TaskRevision.model_validate(task_payload())
        self.assertEqual(task.track, Track.AGENTS)
        self.assertEqual(len(task.digest()), 64)

    def test_task_rejects_floating_image_and_path_escape(self) -> None:
        with self.assertRaises(ValueError):
            V2TaskRevision.model_validate(task_payload(official_image="python:3.12"))
        with self.assertRaises(ValueError):
            V2TaskRevision.model_validate(task_payload(allowed_paths=["../secret"]))

    def test_task_rejects_unknown_schema_and_mismatched_provenance(self) -> None:
        with self.assertRaises(ValueError):
            V2TaskRevision.model_validate(task_payload(extra="unknown"))
        source = {**task_payload()["source"], "license_id": "MIT"}
        with self.assertRaises(ValueError):
            V2TaskRevision.model_validate(task_payload(source=source))

    def test_network_policy_and_track_protocols_are_strict(self) -> None:
        with self.assertRaises(ValueError):
            V2NetworkPolicy(mode="none", allowed_hosts=("api.example",))
        protocol = TrackAProtocol(schema_version="aieb.track-a-protocol/v2", id="a", protocol_digest=DIGEST, repetitions=2, max_replacements=1, require_hidden_checks=True)
        self.assertEqual(protocol.track, Track.AGENTS)
        model_protocol = TrackBProtocol(schema_version="aieb.track-b-protocol/v2", id="b", protocol_digest=DIGEST, repetitions=1, max_retries=2, context_limit_tokens=1000, stopping_rule="submit")
        self.assertEqual(model_protocol.track, Track.MODELS)

    def test_campaign_requires_exact_matrix(self) -> None:
        base = {"schema_version": "aieb.campaign/v2", "id": UUID("00000000-0000-0000-0000-000000000001"), "release_id": "dev-release", "track": "agents", "task_ids": ["task-a"], "entrant_ids": ["agent-a", "agent-b"], "repetitions": 2}
        cells = [CampaignCell(trial_id=UUID(int=i + 1), task_id="task-a", entrant_id=entrant, repetition=rep, order_index=i) for i, (entrant, rep) in enumerate((("agent-a", 0), ("agent-a", 1), ("agent-b", 0), ("agent-b", 1)))]
        campaign = V2Campaign.model_validate({**base, "cells": cells})
        self.assertEqual(len(campaign.cells), 4)
        with self.assertRaises(ValueError):
            V2Campaign.model_validate({**base, "cells": cells[:-1]})

    def test_snapshot_keeps_correctness_and_reliability_separate(self) -> None:
        snapshot = PublicationSnapshot(schema_version="aieb.publication-snapshot/v2", id=UUID(int=1), release_id="r", cohort_id="c", release_digest=DIGEST, snapshot_digest=DIGEST, correctness_rate="0.75", reliability_rate="0.9", valid_trials=3, total_trials=4)
        self.assertNotEqual(snapshot.correctness_rate, snapshot.reliability_rate)
        with self.assertRaises(ValueError):
            PublicationSnapshot.model_validate({**snapshot.model_dump(mode="json"), "valid_trials": 5, "total_trials": 4})


if __name__ == "__main__":
    unittest.main()
