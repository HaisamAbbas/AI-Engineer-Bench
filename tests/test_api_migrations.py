"""ENG-014 migration compatibility: upgrade -> downgrade -> upgrade against real PostgreSQL.

Requires AIEB_DATABASE_URL; skipped (not faked against sqlite) when unset.
"""
from __future__ import annotations

import os
import json
import hashlib
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "services/api"
DATABASE_URL = os.environ.get("AIEB_DATABASE_URL")


def _alembic(*args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["AIEB_DATABASE_URL"] = DATABASE_URL or ""
    # The interpreter running these tests is the project venv's own Python
    # (invoked as `.venv/Scripts/python.exe` on Windows or `.venv/bin/python`
    # on Unix, or via `uv run python`); `sys.executable` resolves to it on
    # every platform. A hardcoded `.venv/Scripts/python.exe` only exists on
    # Windows and made this test fail with FileNotFoundError on the Ubuntu CI.
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args], cwd=API_DIR, env=env, capture_output=True, text=True,
    )


@unittest.skipUnless(DATABASE_URL, "AIEB_DATABASE_URL is not set; ENG-014 real-Postgres tests are blocked")
class MigrationCompatibilityTests(unittest.TestCase):
    def test_upgrade_downgrade_upgrade_round_trip(self) -> None:
        base = _alembic("downgrade", "base")
        self.assertEqual(base.returncode, 0, base.stderr)

        # Seed the exact previous schema before applying the hardening
        # migration so its retention and candidate ownership backfill is
        # exercised against preexisting artifact rows.
        up_legacy = _alembic("upgrade", "e20d5d09b489")
        self.assertEqual(up_legacy.returncode, 0, up_legacy.stderr)
        legacy_rows = self._seed_legacy_artifacts()

        up_first = _alembic("upgrade", "head")
        self.assertEqual(up_first.returncode, 0, up_first.stderr)
        self.assertIn("upgrade", up_first.stderr.lower())
        self._assert_task_authoring_schema()
        self._assert_backfilled(legacy_rows)

        down_one = _alembic("downgrade", "-1")
        self.assertEqual(down_one.returncode, 0, down_one.stderr)
        self.assertIn("downgrade", down_one.stderr.lower())

        up_again = _alembic("upgrade", "head")
        self.assertEqual(up_again.returncode, 0, up_again.stderr)

        current = _alembic("current")
        self.assertEqual(current.returncode, 0, current.stderr)
        self.assertIn("(head)", current.stdout)

    def _assert_task_authoring_schema(self) -> None:
        engine = create_engine(DATABASE_URL)
        with engine.connect() as connection:
            columns = set(connection.execute(text("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'task_draft'
            """)).scalars())
        engine.dispose()
        self.assertTrue({"manifest", "ticket_text", "source_strategy", "source_metadata", "status"}.issubset(columns))

    def _seed_legacy_artifacts(self) -> dict[str, str]:
        engine = create_engine(DATABASE_URL)
        evaluator_id, task_id, task_v2_id, entrant_id = (str(uuid4()) for _ in range(4))
        campaign_id, trial_id, attempt_id, candidate_id, other_candidate_id = (str(uuid4()) for _ in range(5))
        references = {key: str(uuid4()) for key in ("claimed", "wrong_scope", "wrong_digest", "wrong_length", "ambiguous", "unclaimed")}
        blobs = {key: f"{letter}" * 64 for key, letter in zip(references, "abcdef", strict=True)}

        def reference_entry(key: str, *, digest: str | None = None, length: int = 1, scope: str = attempt_id) -> dict:
            return {
                "path": f"service/{key}.py",
                "reference": {
                    "id": references[key],
                    "blob": {"sha256": digest or blobs[key], "byte_length": length},
                    "access_scope": scope,
                    "visibility": "restricted",
                },
            }

        stored_candidate = {
            "manifest": {},
            "file_references": [
                reference_entry("claimed"),
                reference_entry("wrong_scope", scope=str(uuid4())),
                reference_entry("wrong_digest", digest=blobs["claimed"]),
                reference_entry("wrong_length", length=2),
                reference_entry("ambiguous"),
            ],
        }
        other_candidate = {
            "manifest": {},
            "file_references": [reference_entry("ambiguous")],
        }
        with engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO evaluator_revision (id, code_digest, contract_version, review_status)
                VALUES (:id, :digest, 'v1', 'reviewed')
            """), {"id": evaluator_id, "digest": "e" * 64})
            connection.execute(text("""
                INSERT INTO task_revision (id, slug, version, family_id, category, source_digest, manifest_digest, evaluator_id, manifest)
                VALUES (:id, 'rag.document-freshness', '0.1.0', 'knowledge-service-a', 'rag', :source, :manifest_digest, :evaluator, '{}'::jsonb)
            """), {"id": task_id, "source": "s" * 64, "manifest_digest": "m" * 64, "evaluator": evaluator_id})
            connection.execute(text("""
                INSERT INTO task_revision (id, slug, version, family_id, category, source_digest, manifest_digest, evaluator_id, manifest)
                VALUES (:id, 'rag.document-freshness', '0.2.0', 'knowledge-service-a', 'rag', :source, :manifest_digest, :evaluator, '{}'::jsonb)
            """), {"id": task_v2_id, "source": "s" * 64, "manifest_digest": "n" * 64, "evaluator": evaluator_id})
            connection.execute(text("""
                INSERT INTO entrant_revision (id, slug, version, track, config_digest, capabilities, manifest)
                VALUES (:id, 'migration.entrant', 'v1', 'models', :digest, '{}'::jsonb, '{}'::jsonb)
            """), {"id": entrant_id, "digest": "c" * 64})
            connection.execute(text("""
                INSERT INTO campaign (id, name, state, draft, resolved, revision)
                VALUES (:id, 'migration test', 'draft', '{}'::jsonb, CAST(:resolved AS jsonb), 0)
            """), {
                "id": campaign_id,
                "resolved": json.dumps({"protocol": {
                    "schema_version": "aieb.protocol/v1", "id": "migration-protocol-v1",
                    "scoring_digest": "p" * 64, "max_replacements": 1,
                    "required_trace_coverage": True, "hard_cost_ranking": False,
                }}),
            })
            connection.execute(text("""
                INSERT INTO trial (id, campaign_id, task_revision_id, entrant_revision_id, repetition, cell_digest)
                VALUES (:id, :campaign, :task, :entrant, 0, :digest)
            """), {"id": trial_id, "campaign": campaign_id, "task": task_id, "entrant": entrant_id, "digest": "t" * 64})
            connection.execute(text("""
                INSERT INTO attempt (id, trial_id, number, phase, lease_generation)
                VALUES (:id, :trial, 1, 'verifying', 0)
            """), {"id": attempt_id, "trial": trial_id})
            connection.execute(text("""
                INSERT INTO candidate (id, attempt_id, tree_digest, manifest_digest, validation_status, stored_candidate)
                VALUES (:id, :attempt, :tree, :manifest_digest, 'valid', CAST(:stored AS jsonb))
            """), {
                "id": candidate_id, "attempt": attempt_id, "tree": "d" * 64, "manifest_digest": "f" * 64,
                "stored": json.dumps(stored_candidate),
            })
            connection.execute(text("""
                INSERT INTO candidate (id, attempt_id, tree_digest, manifest_digest, validation_status, stored_candidate)
                VALUES (:id, :attempt, :tree, :manifest_digest, 'valid', CAST(:stored AS jsonb))
            """), {
                "id": other_candidate_id, "attempt": attempt_id, "tree": "9" * 64,
                "manifest_digest": "8" * 64, "stored": json.dumps(other_candidate),
            })
            for digest in blobs.values():
                connection.execute(text("""
                    INSERT INTO worker_artifact_blob (sha256, byte_length, data, created_at)
                    VALUES (:digest, 1, :data, now() - INTERVAL '3 days')
                """), {"digest": digest, "data": b"x"})
            connection.execute(text("""
                INSERT INTO worker_artifact_reference (id, blob_sha256, access_scope, visibility)
                VALUES (:claimed, :claimed_blob, :scope, 'restricted'),
                       (:wrong_scope, :wrong_scope_blob, :scope, 'restricted'),
                       (:wrong_digest, :wrong_digest_blob, :scope, 'restricted'),
                       (:wrong_length, :wrong_length_blob, :scope, 'restricted'),
                       (:ambiguous, :ambiguous_blob, :scope, 'restricted'),
                       (:unclaimed, :unclaimed_blob, :scope, 'restricted')
            """), {
                "claimed": references["claimed"], "claimed_blob": blobs["claimed"],
                "wrong_scope": references["wrong_scope"], "wrong_scope_blob": blobs["wrong_scope"],
                "wrong_digest": references["wrong_digest"], "wrong_digest_blob": blobs["wrong_digest"],
                "wrong_length": references["wrong_length"], "wrong_length_blob": blobs["wrong_length"],
                "ambiguous": references["ambiguous"], "ambiguous_blob": blobs["ambiguous"],
                "unclaimed": references["unclaimed"], "unclaimed_blob": blobs["unclaimed"], "scope": attempt_id,
            })
        engine.dispose()
        return {"candidate": candidate_id, "task_v2": task_v2_id, **{f"reference_{key}": value for key, value in references.items()}, **{f"blob_{key}": value for key, value in blobs.items()}}

    def _assert_backfilled(self, rows: dict[str, str]) -> None:
        engine = create_engine(DATABASE_URL)
        with engine.connect() as connection:
            references = connection.execute(text("""
                SELECT id::text, candidate_id::text FROM worker_artifact_reference
                WHERE id::text IN (:claimed, :wrong_scope, :wrong_digest, :wrong_length, :ambiguous, :unclaimed)
            """), {
                "claimed": rows["reference_claimed"], "wrong_scope": rows["reference_wrong_scope"],
                "wrong_digest": rows["reference_wrong_digest"], "wrong_length": rows["reference_wrong_length"],
                "ambiguous": rows["reference_ambiguous"], "unclaimed": rows["reference_unclaimed"],
            }).all()
            blobs = connection.execute(text("""
                SELECT sha256, retention_class, staged_until, created_at
                FROM worker_artifact_blob WHERE sha256 IN (:claimed, :wrong_scope, :wrong_digest, :wrong_length, :ambiguous, :unclaimed)
            """), {
                "claimed": rows["blob_claimed"], "wrong_scope": rows["blob_wrong_scope"],
                "wrong_digest": rows["blob_wrong_digest"], "wrong_length": rows["blob_wrong_length"],
                "ambiguous": rows["blob_ambiguous"], "unclaimed": rows["blob_unclaimed"],
            }).all()
            ticket = connection.execute(text("""
                SELECT ticket_text, revision_digest, manifest FROM task_revision WHERE slug = 'rag.document-freshness' AND version = '0.1.0'
            """)).one()
            wrong_version = connection.execute(text("""
                SELECT ticket_text, revision_digest, manifest FROM task_revision WHERE id = :id
            """), {"id": rows["task_v2"]}).one()
            protocol = connection.execute(text("""
                SELECT version, scoring_digest, manifest->>'id' FROM protocol_revision
                WHERE version = 'migration-protocol-v1'
            """)).one()
            candidate_evidence = connection.execute(text("""
                SELECT stored_candidate, stored_candidate_digest FROM candidate WHERE id=:id
            """), {"id": rows["candidate"]}).one()
        engine.dispose()
        reference_owners = dict(references)
        self.assertEqual(reference_owners[rows["reference_claimed"]], rows["candidate"])
        for key in ("wrong_scope", "wrong_digest", "wrong_length", "ambiguous", "unclaimed"):
            self.assertIsNone(reference_owners[rows[f"reference_{key}"]], key)
        blob_rows = {row.sha256: row for row in blobs}
        self.assertIn("Updated documents can return old passages", ticket[0])
        self.assertEqual(len(ticket[1]), 64)
        self.assertIsNone(wrong_version.ticket_text)
        self.assertEqual(len(wrong_version.revision_digest), 64)
        expected_revision = {
            "schema_version": "aieb.task-revision-identity/v1",
            "manifest": ticket.manifest,
            "ticket_text": ticket.ticket_text,
        }
        self.assertEqual(
            ticket.revision_digest,
            hashlib.sha256(json.dumps(expected_revision, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest(),
        )
        self.assertEqual(
            candidate_evidence.stored_candidate_digest,
            hashlib.sha256(json.dumps(candidate_evidence.stored_candidate, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest(),
        )
        self.assertEqual(protocol, ("migration-protocol-v1", "p" * 64, "migration-protocol-v1"))
        self.assertEqual(blob_rows[rows["blob_claimed"]].retention_class, "evidence")
        self.assertIsNone(blob_rows[rows["blob_claimed"]].staged_until)
        for key in ("wrong_scope", "wrong_digest", "wrong_length", "ambiguous", "unclaimed"):
            blob = blob_rows[rows[f"blob_{key}"]]
            self.assertEqual(blob.retention_class, "staging", key)
            self.assertIsNotNone(blob.staged_until, key)
            expected_expiry = blob.created_at + timedelta(hours=24)
            self.assertLess(abs((blob.staged_until - expected_expiry).total_seconds()), 1)
            self.assertLess(blob.staged_until, datetime.now(timezone.utc))


if __name__ == "__main__":
    unittest.main()
