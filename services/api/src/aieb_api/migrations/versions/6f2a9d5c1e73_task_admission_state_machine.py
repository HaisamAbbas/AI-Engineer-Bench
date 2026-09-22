"""V2-GAP-003: persisted task admission state machine

Freezing a draft only pins immutable revision identity; it is not admission.
This migration adds the persisted, immutably-enforced admission machinery:

- task_admission_state   one row per frozen revision, holding the current
                         state-machine state with a trigger that only permits
                         the declared transitions and never rewrites authorship;
- task_admission_run     one row per admission execution attempt, pinning the
                         exact revision/manifest/source/evaluator/protocol
                         digests it executed against, immutably once terminal;
- task_admission_gate    one row per protocol gate per run, finalized exactly
                         once (not_run -> pass/fail/indeterminate) and then
                         immutable;
- task_admission_reset   append-only clean-reset evidence rows;
- task_admission_review  append-only independent review records with database
                         checks that the reviewer is not the author or the
                         requester of the run under review.

Only one non-terminal run may exist per revision (partial unique index), so
concurrent admission requests cannot create two active runs. Existing task
revision rows are backfilled as `frozen` - frozen identity only, never admitted.

Revision: 6f2a9d5c1e73
Revises: cd3e4f5a6b7c
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "6f2a9d5c1e73"
down_revision = "cd3e4f5a6b7c"
branch_labels = None
depends_on = None

_STATE_VALUES = "'frozen','admission_pending','admission_running','admission_failed','pending_independent_review','admitted','rejected'"
_RUN_VALUES = "'pending','running','passed','failed','cancelled'"
_GATE_VALUES = "'not_run','pass','fail','indeterminate'"


def upgrade() -> None:
    op.create_table(
        "task_admission_state",
        sa.Column("task_revision_id", sa.UUID(), sa.ForeignKey("task_revision.id"), primary_key=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("author_user_id", sa.UUID(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(f"status in ({_STATE_VALUES})", name="ck_task_admission_state_status"),
    )

    op.create_table(
        "task_admission_run",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("task_revision_id", sa.UUID(), sa.ForeignKey("task_revision.id"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("requested_by_user_id", sa.UUID(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("protocol_version", sa.String(64), nullable=False),
        sa.Column("protocol_digest", sa.String(64), nullable=False),
        sa.Column("revision_digest", sa.String(64), nullable=False),
        sa.Column("manifest_digest", sa.String(64), nullable=False),
        sa.Column("source_digest", sa.String(64), nullable=False),
        sa.Column("evaluator_digest", sa.String(64), nullable=False),
        sa.Column("result_digest", sa.String(64), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(f"status in ({_RUN_VALUES})", name="ck_task_admission_run_status"),
        sa.Index("ix_task_admission_run_revision", "task_revision_id"),
    )
    # One ACTIVE (pending/running) run per revision, enforced by the database so
    # two concurrent admission requests cannot both win; completed/failed/cancelled
    # runs accumulate freely as history.
    op.create_index(
        "uq_task_admission_run_active",
        "task_admission_run",
        ["task_revision_id"],
        unique=True,
        postgresql_where=sa.text("status in ('pending','running')"),
    )

    op.create_table(
        "task_admission_gate",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("admission_run_id", sa.UUID(), sa.ForeignKey("task_admission_run.id", ondelete="CASCADE"), nullable=False),
        sa.Column("gate_name", sa.String(64), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("status", sa.String(16), nullable=False, server_default="not_run"),
        sa.Column("observed_digest", sa.String(64), nullable=True),
        sa.Column("evidence_reference", sa.Text(), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(f"status in ({_GATE_VALUES})", name="ck_task_admission_gate_status"),
        sa.UniqueConstraint("admission_run_id", "gate_name", name="uq_task_admission_gate_run_name"),
    )

    op.create_table(
        "task_admission_reset",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("admission_run_id", sa.UUID(), sa.ForeignKey("task_admission_run.id", ondelete="CASCADE"), nullable=False),
        sa.Column("matrix_case_id", sa.String(64), nullable=False),
        sa.Column("reset_number", sa.Integer(), nullable=False),
        sa.Column("clean_digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("evidence_reference", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("reset_number > 0", name="ck_task_admission_reset_number"),
        sa.CheckConstraint("status in ('pass','fail')", name="ck_task_admission_reset_status"),
        sa.UniqueConstraint("admission_run_id", "matrix_case_id", "reset_number", name="uq_task_admission_reset_case"),
    )

    op.create_table(
        "task_admission_review",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("task_revision_id", sa.UUID(), sa.ForeignKey("task_revision.id"), nullable=False),
        sa.Column("admission_run_id", sa.UUID(), sa.ForeignKey("task_admission_run.id"), nullable=False),
        sa.Column("reviewer_user_id", sa.UUID(), sa.ForeignKey("users.id"), nullable=False),
        # Denormalized so the independence rule is a database check rather than a
        # route-level convention: a review row cannot be written naming the run's
        # own requester, or the revision's author, as its reviewer.
        sa.Column("author_user_id", sa.UUID(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("requested_by_user_id", sa.UUID(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("scope", sa.String(64), nullable=False),
        sa.Column("evidence_digest", sa.String(64), nullable=False),
        sa.Column("independence_declaration", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("decision in ('approve','reject')", name="ck_task_admission_review_decision"),
        sa.CheckConstraint("independence_declaration = true", name="ck_task_admission_review_independence"),
        sa.CheckConstraint(
            "(author_user_id is null or reviewer_user_id <> author_user_id) "
            "and reviewer_user_id <> requested_by_user_id",
            name="ck_task_admission_review_independent",
        ),
        sa.UniqueConstraint("admission_run_id", name="uq_task_admission_review_run"),
        sa.Index("ix_task_admission_review_revision", "task_revision_id"),
    )

    # --- persistence-level enforcement (not route conventions) ----------------
    # Admission state may only walk the declared transitions, must be inserted as
    # `frozen`, and its author identity is never rewritten.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION aieb_enforce_admission_state() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.status <> 'frozen' THEN
                    RAISE EXCEPTION 'admission state must be inserted as frozen, not %', NEW.status
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                RETURN NEW;
            END IF;
            IF NEW.author_user_id IS DISTINCT FROM OLD.author_user_id THEN
                RAISE EXCEPTION 'admission author identity is immutable'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            IF NEW.status = OLD.status THEN
                RETURN NEW;
            END IF;
            IF NOT (
                (OLD.status = 'frozen' AND NEW.status = 'admission_pending')
                OR (OLD.status = 'admission_pending' AND NEW.status IN ('admission_running', 'admission_failed'))
                OR (OLD.status = 'admission_running' AND NEW.status IN ('admission_failed', 'pending_independent_review'))
                OR (OLD.status = 'admission_failed' AND NEW.status = 'admission_pending')
                OR (OLD.status = 'pending_independent_review' AND NEW.status IN ('admitted', 'rejected', 'admission_pending'))
            ) THEN
                RAISE EXCEPTION 'illegal admission transition: % -> %', OLD.status, NEW.status
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            IF NEW.status = 'admitted' AND NOT EXISTS (
                SELECT 1 FROM task_admission_review r
                WHERE r.task_revision_id = NEW.task_revision_id AND r.decision = 'approve'
                  AND r.admission_run_id = (
                      SELECT ar.id FROM task_admission_run ar
                      WHERE ar.task_revision_id = NEW.task_revision_id
                      ORDER BY ar.created_at DESC, ar.id DESC LIMIT 1
                  )
            ) THEN
                RAISE EXCEPTION 'admitted state requires an approving independent review'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            IF NEW.status = 'rejected' AND NOT EXISTS (
                SELECT 1 FROM task_admission_review r
                WHERE r.task_revision_id = NEW.task_revision_id AND r.decision = 'reject'
                  AND r.admission_run_id = (
                      SELECT ar.id FROM task_admission_run ar
                      WHERE ar.task_revision_id = NEW.task_revision_id
                      ORDER BY ar.created_at DESC, ar.id DESC LIMIT 1
                  )
            ) THEN
                RAISE EXCEPTION 'rejected state requires a rejecting independent review'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute("CREATE TRIGGER task_admission_state_insert BEFORE INSERT ON task_admission_state FOR EACH ROW EXECUTE FUNCTION aieb_enforce_admission_state();")
    op.execute("CREATE TRIGGER task_admission_state_transition BEFORE UPDATE ON task_admission_state FOR EACH ROW EXECUTE FUNCTION aieb_enforce_admission_state();")

    # A terminal run is immutable evidence, and a run can never be re-pointed at a
    # different revision/digest set after the fact.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION aieb_reject_admission_run_mutation() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.status <> 'pending' THEN
                    RAISE EXCEPTION 'admission runs must be inserted as pending, not %', NEW.status
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                RETURN NEW;
            END IF;
            IF TG_OP = 'UPDATE' THEN
                IF OLD.status IN ('passed', 'failed', 'cancelled') THEN
                    RAISE EXCEPTION 'admission run % is % and immutable; start a new run instead', OLD.id, OLD.status
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                IF NEW.task_revision_id IS DISTINCT FROM OLD.task_revision_id
                    OR NEW.revision_digest IS DISTINCT FROM OLD.revision_digest
                    OR NEW.manifest_digest IS DISTINCT FROM OLD.manifest_digest
                    OR NEW.source_digest IS DISTINCT FROM OLD.source_digest
                    OR NEW.evaluator_digest IS DISTINCT FROM OLD.evaluator_digest
                    OR NEW.protocol_digest IS DISTINCT FROM OLD.protocol_digest
                    OR NEW.protocol_version IS DISTINCT FROM OLD.protocol_version
                    OR NEW.requested_by_user_id IS DISTINCT FROM OLD.requested_by_user_id THEN
                    RAISE EXCEPTION 'admission run identity and digests are immutable'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'admission runs are never deleted'
                USING ERRCODE = 'integrity_constraint_violation';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute("CREATE TRIGGER task_admission_run_mutation BEFORE INSERT OR UPDATE OR DELETE ON task_admission_run FOR EACH ROW EXECUTE FUNCTION aieb_reject_admission_run_mutation();")

    # Each gate finalizes exactly once; a finalized gate is immutable evidence.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION aieb_reject_admission_gate_mutation() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NOT EXISTS (
                    SELECT 1 FROM task_admission_run r
                    WHERE r.id = NEW.admission_run_id AND r.status IN ('pending','running')
                ) THEN
                    RAISE EXCEPTION 'gate evidence may only be created while the admission run is active'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                RETURN NEW;
            END IF;
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'admission gate rows are never deleted'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            IF OLD.status <> 'not_run' THEN
                RAISE EXCEPTION 'admission gate % on run % already finalized as %', OLD.gate_name, OLD.admission_run_id, OLD.status
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            IF NEW.admission_run_id IS DISTINCT FROM OLD.admission_run_id
                OR NEW.gate_name IS DISTINCT FROM OLD.gate_name
                OR NEW.required IS DISTINCT FROM OLD.required THEN
                RAISE EXCEPTION 'admission gate identity is immutable'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute("CREATE TRIGGER task_admission_gate_mutation BEFORE INSERT OR UPDATE OR DELETE ON task_admission_gate FOR EACH ROW EXECUTE FUNCTION aieb_reject_admission_gate_mutation();")

    # Reset evidence is append-only and may only be appended while its parent
    # run is actually running.  This closes the otherwise-valid SQL path that
    # could append evidence to a terminal, supposedly immutable run.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION aieb_guard_admission_reset() RETURNS trigger AS $$
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'task_admission_reset rows are append-only'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM task_admission_run r
                WHERE r.id = NEW.admission_run_id AND r.status = 'running'
            ) THEN
                RAISE EXCEPTION 'reset evidence may only be written while the admission run is running'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute("CREATE TRIGGER task_admission_reset_guard BEFORE INSERT OR UPDATE OR DELETE ON task_admission_reset FOR EACH ROW EXECUTE FUNCTION aieb_guard_admission_reset();")

    # Reviews are append-only and cross-checked against their run/state at the
    # database boundary, including the denormalized independence identities.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION aieb_guard_admission_review() RETURNS trigger AS $$
        DECLARE
            run_row task_admission_run%ROWTYPE;
            state_row task_admission_state%ROWTYPE;
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'task_admission_review rows are append-only'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            SELECT * INTO run_row FROM task_admission_run WHERE id = NEW.admission_run_id;
            SELECT * INTO state_row FROM task_admission_state WHERE task_revision_id = NEW.task_revision_id;
            IF run_row.id IS NULL OR run_row.task_revision_id <> NEW.task_revision_id
                OR run_row.status <> 'passed' OR run_row.result_digest <> NEW.evidence_digest THEN
                RAISE EXCEPTION 'review does not bind to a passing run and its exact evidence digest'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            IF state_row.status <> 'pending_independent_review'
                OR NEW.requested_by_user_id <> run_row.requested_by_user_id
                OR NEW.author_user_id IS DISTINCT FROM state_row.author_user_id THEN
                RAISE EXCEPTION 'review identity/state does not match the admission record'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute("CREATE TRIGGER task_admission_review_guard BEFORE INSERT OR UPDATE OR DELETE ON task_admission_review FOR EACH ROW EXECUTE FUNCTION aieb_guard_admission_review();")

    # Generic append-only helper remains available for future evidence tables.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION aieb_reject_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '% rows are append-only', TG_TABLE_NAME
                USING ERRCODE = 'integrity_constraint_violation';
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    # Backfill: every pre-existing revision is frozen identity only. Nothing is
    # admitted here - admission requires executed gates plus an independent review.
    op.execute(
        """
        INSERT INTO task_admission_state (task_revision_id, status, author_user_id)
        SELECT tr.id, 'frozen', td.created_by_user_id
        FROM task_revision tr
        LEFT JOIN task_draft td ON td.frozen_revision_id = tr.id
        ON CONFLICT (task_revision_id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS task_admission_review_guard ON task_admission_review;")
    op.execute("DROP TRIGGER IF EXISTS task_admission_reset_guard ON task_admission_reset;")
    op.execute("DROP TRIGGER IF EXISTS task_admission_gate_mutation ON task_admission_gate;")
    op.execute("DROP TRIGGER IF EXISTS task_admission_run_mutation ON task_admission_run;")
    op.execute("DROP TRIGGER IF EXISTS task_admission_state_transition ON task_admission_state;")
    op.execute("DROP TRIGGER IF EXISTS task_admission_state_insert ON task_admission_state;")
    op.execute("DROP FUNCTION IF EXISTS aieb_reject_append_only();")
    op.execute("DROP FUNCTION IF EXISTS aieb_guard_admission_review();")
    op.execute("DROP FUNCTION IF EXISTS aieb_guard_admission_reset();")
    op.execute("DROP FUNCTION IF EXISTS aieb_reject_admission_gate_mutation();")
    op.execute("DROP FUNCTION IF EXISTS aieb_reject_admission_run_mutation();")
    op.execute("DROP FUNCTION IF EXISTS aieb_enforce_admission_state();")
    op.drop_table("task_admission_review")
    op.drop_table("task_admission_reset")
    op.drop_table("task_admission_gate")
    op.drop_index("uq_task_admission_run_active", table_name="task_admission_run")
    op.drop_table("task_admission_run")
    op.drop_table("task_admission_state")
