"""Bind published run selections and displayed evidence to immutable digests.

Revision ID: c8d41a6e2b90
Revises: b516f7d2c841
"""
from __future__ import annotations

import hashlib
import json

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c8d41a6e2b90"
down_revision = "b516f7d2c841"
branch_labels = None
depends_on = None

KNOWN_DEV_TASKS = {
    "ext.batch-alignment", "ext.missingness", "ext.partial-batch", "ext.unit-normalization",
    "rag.citation-current-span", "rag.document-freshness", "rag.embedding-version",
    "rag.metadata-filter-topk", "tool.corrected-arguments", "tool.false-completion",
    "tool.idempotent-write", "tool.session-isolation",
}


def _digest(value: dict) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def upgrade() -> None:
    op.add_column("task_revision", sa.Column("revision_digest", sa.String(length=64), nullable=True))
    op.add_column("candidate", sa.Column("stored_candidate_digest", sa.String(length=64), nullable=True))
    op.add_column("evaluation", sa.Column("evaluation_digest", sa.String(length=64), nullable=True))
    op.add_column("publication", sa.Column("evidence_manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("publication", sa.Column("evidence_manifest_digest", sa.String(length=64), nullable=True))
    op.create_check_constraint(
        "ck_publication_evidence_manifest_digest_pair", "publication",
        "(evidence_manifest IS NULL) = (evidence_manifest_digest IS NULL)",
    )

    bind = op.get_bind()
    op.execute("DROP TRIGGER task_revision_immutable ON task_revision")
    rows = bind.execute(sa.text("SELECT id, slug, version, manifest, ticket_text FROM task_revision")).mappings().all()
    for row in rows:
        # The prior migration backfilled by slug alone. Only the known release
        # revision has an authoritative ticket. Clear prose copied onto any
        # other version before binding the complete revision identity.
        ticket = row["ticket_text"] if row["slug"] in KNOWN_DEV_TASKS and row["version"] == "0.1.0" else None
        bind.execute(
            sa.text("UPDATE task_revision SET ticket_text=:ticket, revision_digest=:digest WHERE id=:id"),
            {"id": row["id"], "ticket": ticket, "digest": _digest({
                "schema_version": "aieb.task-revision-identity/v1",
                "manifest": row["manifest"],
                "ticket_text": ticket,
            })},
        )
    op.alter_column("task_revision", "revision_digest", nullable=False)
    op.execute("""
        CREATE TRIGGER task_revision_immutable
        BEFORE UPDATE ON task_revision
        FOR EACH ROW EXECUTE FUNCTION aieb_reject_revision_update();
    """)

    candidates = bind.execute(sa.text("SELECT id, stored_candidate FROM candidate")).mappings().all()
    for row in candidates:
        bind.execute(
            sa.text("UPDATE candidate SET stored_candidate_digest=:digest WHERE id=:id"),
            {"id": row["id"], "digest": _digest(row["stored_candidate"])},
        )
    op.alter_column("candidate", "stored_candidate_digest", nullable=False)
    op.execute("""
        CREATE FUNCTION aieb_reject_candidate_evidence_mutation() RETURNS trigger AS $$
        BEGIN
            IF NEW.attempt_id IS DISTINCT FROM OLD.attempt_id
               OR NEW.validation_status IS DISTINCT FROM OLD.validation_status
               OR NEW.stored_candidate IS DISTINCT FROM OLD.stored_candidate
               OR NEW.stored_candidate_digest IS DISTINCT FROM OLD.stored_candidate_digest
               OR NEW.manifest_digest IS DISTINCT FROM OLD.manifest_digest
               OR NEW.tree_digest IS DISTINCT FROM OLD.tree_digest THEN
                RAISE EXCEPTION 'candidate % evidence is immutable', OLD.id
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER candidate_evidence_immutable BEFORE UPDATE ON candidate
        FOR EACH ROW EXECUTE FUNCTION aieb_reject_candidate_evidence_mutation();
    """)

    evaluations = bind.execute(sa.text("""
        SELECT id, candidate_id, evaluator_id, fixture_id, schedule_digest, verdict, result FROM evaluation
    """)).mappings().all()
    for row in evaluations:
        bind.execute(
            sa.text("UPDATE evaluation SET evaluation_digest=:digest WHERE id=:id"),
            {"id": row["id"], "digest": _digest({
                "candidate_id": str(row["candidate_id"]), "evaluator_id": str(row["evaluator_id"]),
                "fixture_id": str(row["fixture_id"]), "schedule_digest": row["schedule_digest"],
                "verdict": row["verdict"], "result": row["result"],
            })},
        )
    op.alter_column("evaluation", "evaluation_digest", nullable=False)
    op.execute("""
        CREATE FUNCTION aieb_reject_evaluation_evidence_mutation() RETURNS trigger AS $$
        BEGIN
            IF NEW.candidate_id IS DISTINCT FROM OLD.candidate_id OR NEW.evaluator_id IS DISTINCT FROM OLD.evaluator_id
               OR NEW.fixture_id IS DISTINCT FROM OLD.fixture_id OR NEW.schedule_digest IS DISTINCT FROM OLD.schedule_digest
               OR NEW.verdict IS DISTINCT FROM OLD.verdict OR NEW.result IS DISTINCT FROM OLD.result
               OR NEW.evaluation_digest IS DISTINCT FROM OLD.evaluation_digest THEN
                RAISE EXCEPTION 'evaluation % evidence is immutable', OLD.id
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER evaluation_evidence_immutable BEFORE UPDATE ON evaluation
        FOR EACH ROW EXECUTE FUNCTION aieb_reject_evaluation_evidence_mutation();
    """)

    # Existing publications have no recorded trial/evaluation selection.
    # Leave these columns NULL so public evidence reads fail closed until a
    # new publication is created with an explicit manifest.
    op.execute("""
        CREATE OR REPLACE FUNCTION aieb_reject_publication_snapshot_mutation() RETURNS trigger AS $$
        BEGIN
            IF NEW.snapshot IS DISTINCT FROM OLD.snapshot OR NEW.snapshot_digest IS DISTINCT FROM OLD.snapshot_digest
               OR NEW.evidence_manifest IS DISTINCT FROM OLD.evidence_manifest
               OR NEW.evidence_manifest_digest IS DISTINCT FROM OLD.evidence_manifest_digest THEN
                RAISE EXCEPTION 'publication % snapshot and evidence manifest are immutable; create a new publication instead', OLD.id
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS candidate_evidence_immutable ON candidate")
    op.execute("DROP FUNCTION IF EXISTS aieb_reject_candidate_evidence_mutation()")
    op.execute("DROP TRIGGER IF EXISTS evaluation_evidence_immutable ON evaluation")
    op.execute("DROP FUNCTION IF EXISTS aieb_reject_evaluation_evidence_mutation()")
    op.drop_constraint("ck_publication_evidence_manifest_digest_pair", "publication", type_="check")
    op.drop_column("publication", "evidence_manifest_digest")
    op.drop_column("publication", "evidence_manifest")
    op.drop_column("candidate", "stored_candidate_digest")
    op.drop_column("evaluation", "evaluation_digest")
    op.execute("DROP TRIGGER IF EXISTS task_revision_immutable ON task_revision")
    op.drop_column("task_revision", "revision_digest")
    op.execute("""
        CREATE TRIGGER task_revision_immutable BEFORE UPDATE ON task_revision
        FOR EACH ROW EXECUTE FUNCTION aieb_reject_revision_update();
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION aieb_reject_publication_snapshot_mutation() RETURNS trigger AS $$
        BEGIN
            IF NEW.snapshot IS DISTINCT FROM OLD.snapshot OR NEW.snapshot_digest IS DISTINCT FROM OLD.snapshot_digest THEN
                RAISE EXCEPTION 'publication % snapshot is immutable; create a new publication instead', OLD.id
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
