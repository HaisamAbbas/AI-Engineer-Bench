"""Harden independent-review provenance and target locking.

The first review-authorization migration protected the HTTP routes, but direct
SQL could still omit task authorship, let a campaign creator submit a
publication rejection, race a target transition, or leave no durable proof of
which role grant authorized a reviewer.  This additive migration keeps old
rows readable while requiring complete authorization snapshots for every new
review.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "h1a2b3c4d5e6"
down_revision = "g1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "task_admission_review",
        sa.Column("reviewer_role_binding_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_task_admission_review_role_binding",
        "task_admission_review",
        "role_bindings",
        ["reviewer_role_binding_id"],
        ["id"],
    )
    op.create_index(
        "ix_task_admission_review_role_binding",
        "task_admission_review",
        ["reviewer_role_binding_id"],
    )

    op.add_column(
        "independent_review",
        sa.Column("reviewer_role_binding_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_independent_review_role_binding",
        "independent_review",
        "role_bindings",
        ["reviewer_role_binding_id"],
        ["id"],
    )
    op.create_index(
        "ix_independent_review_role_binding",
        "independent_review",
        ["reviewer_role_binding_id"],
    )

    # A review row keeps the exact grant identity.  Referenced grants may not
    # subsequently be edited or deleted; administrators can revoke an
    # unreferenced grant by deleting it, while historical review evidence stays
    # provable and FK-protected.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION aieb_guard_reviewer_role_binding_history() RETURNS trigger AS $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM task_admission_review
                 WHERE reviewer_role_binding_id = OLD.id
            ) OR EXISTS (
                SELECT 1 FROM independent_review
                 WHERE reviewer_role_binding_id = OLD.id
            ) THEN
                RAISE EXCEPTION 'role binding % is referenced by immutable review evidence', OLD.id
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        DROP TRIGGER IF EXISTS reviewer_role_binding_history_guard ON role_bindings;
        CREATE TRIGGER reviewer_role_binding_history_guard
        BEFORE UPDATE OR DELETE ON role_bindings
        FOR EACH ROW EXECUTE FUNCTION aieb_guard_reviewer_role_binding_history();
        """
    )

    # Replace the release trigger with one that locks the target rows before
    # reading state and checks campaign ownership for both approve and reject.
    # The lock order is campaign -> preparation, matching the API route.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION aieb_guard_release_review_actor_state() RETURNS trigger AS $$
        DECLARE
            expected_subject uuid;
            campaign_creator uuid;
            campaign_id uuid;
            target_state text;
            campaign_state text;
            binding_user uuid;
            binding_role text;
            binding_scope text;
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'independent release reviews are append-only'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;

            IF NEW.target_type = 'campaign_approval' THEN
                SELECT c.created_by_user_id, c.state
                  INTO campaign_creator, target_state
                  FROM campaign c
                 WHERE c.id = NEW.target_id
                 FOR UPDATE;
                IF campaign_creator IS NULL THEN
                    RAISE EXCEPTION 'campaign review requires a recorded campaign creator'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                expected_subject := campaign_creator;
            ELSIF NEW.target_type = 'publication_preparation' THEN
                -- Read the foreign key to identify the campaign, then acquire
                -- both locks in the same order as the API path.
                SELECT pp.campaign_id
                  INTO campaign_id
                  FROM publication_preparation pp
                 WHERE pp.id = NEW.target_id;
                IF campaign_id IS NULL THEN
                    RAISE EXCEPTION 'publication preparation review target does not exist'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                SELECT c.created_by_user_id, c.state
                  INTO campaign_creator, campaign_state
                  FROM campaign c
                 WHERE c.id = campaign_id
                 FOR UPDATE;
                SELECT pp.prepared_by_user_id, pp.status, pp.campaign_id
                  INTO expected_subject, target_state, campaign_id
                  FROM publication_preparation pp
                 WHERE pp.id = NEW.target_id
                 FOR UPDATE;
                IF expected_subject IS NULL OR campaign_creator IS NULL
                   OR campaign_state IS NULL THEN
                    RAISE EXCEPTION 'publication review requires valid preparation and campaign identities'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                IF NEW.reviewer_user_id = campaign_creator THEN
                    RAISE EXCEPTION 'campaign creator cannot review a publication preparation'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
            ELSE
                RAISE EXCEPTION 'unsupported independent review target %', NEW.target_type
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;

            IF (NEW.target_type = 'campaign_approval' AND target_state IS DISTINCT FROM 'planned')
               OR (NEW.target_type = 'publication_preparation' AND target_state IS DISTINCT FROM 'prepared') THEN
                RAISE EXCEPTION 'independent review target is not at its review gate'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            IF expected_subject IS NULL OR NEW.subject_user_id IS DISTINCT FROM expected_subject
               OR NEW.reviewer_user_id = expected_subject THEN
                RAISE EXCEPTION 'independent review subject or reviewer is not independent'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;

            IF NEW.reviewer_role_binding_id IS NULL THEN
                RAISE EXCEPTION 'independent review requires an authorization snapshot'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            SELECT rb.user_id, rb.role, rb.scope
              INTO binding_user, binding_role, binding_scope
              FROM role_bindings rb
             WHERE rb.id = NEW.reviewer_role_binding_id
             FOR SHARE;
            IF binding_user IS NULL OR binding_user <> NEW.reviewer_user_id
               OR binding_role NOT IN ('reviewer','administrator')
               OR binding_scope <> 'global' THEN
                RAISE EXCEPTION 'independent review authorization snapshot is invalid'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            NEW.created_at := clock_timestamp();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    # Replace the task-review role trigger.  The state/review trigger below
    # independently rejects null authors and locks the state row.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION aieb_guard_task_review_actor() RETURNS trigger AS $$
        DECLARE binding_user uuid; binding_role text; binding_scope text;
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RETURN NEW;
            END IF;
            IF NEW.reviewer_role_binding_id IS NULL THEN
                RAISE EXCEPTION 'task admission review requires an authorization snapshot'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            SELECT rb.user_id, rb.role, rb.scope
              INTO binding_user, binding_role, binding_scope
              FROM role_bindings rb
             WHERE rb.id = NEW.reviewer_role_binding_id
             FOR SHARE;
            IF binding_user IS NULL OR binding_user <> NEW.reviewer_user_id
               OR binding_role NOT IN ('reviewer','administrator')
               OR binding_scope <> 'global' THEN
                RAISE EXCEPTION 'task admission review authorization snapshot is invalid'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            NEW.created_at := clock_timestamp();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    # The original state/review trigger is replaced rather than edited in
    # place, so already-applied installations receive the stronger checks.
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
            SELECT * INTO run_row
              FROM task_admission_run
             WHERE id = NEW.admission_run_id
             FOR UPDATE;
            SELECT * INTO state_row
              FROM task_admission_state
             WHERE task_revision_id = NEW.task_revision_id
             FOR UPDATE;
            IF run_row.id IS NULL OR run_row.task_revision_id <> NEW.task_revision_id
                OR run_row.status <> 'passed' OR run_row.result_digest <> NEW.evidence_digest THEN
                RAISE EXCEPTION 'review does not bind to a passing run and its exact evidence digest'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            IF state_row.status <> 'pending_independent_review'
                OR state_row.author_user_id IS NULL
                OR NEW.author_user_id IS NULL
                OR NEW.author_user_id IS DISTINCT FROM state_row.author_user_id
                OR NEW.reviewer_user_id = state_row.author_user_id
                OR NEW.requested_by_user_id <> run_row.requested_by_user_id
                OR NEW.reviewer_role_binding_id IS NULL THEN
                RAISE EXCEPTION 'review identity/state does not establish independent authorship'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            NEW.created_at := clock_timestamp();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    # Direct SQL cannot transition an unknown-author admission into admitted,
    # even if it first inserted a malformed legacy-shaped review row.
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
            IF NEW.status = 'admitted' AND (
                NEW.author_user_id IS NULL OR NOT EXISTS (
                    SELECT 1 FROM task_admission_review r
                    WHERE r.task_revision_id = NEW.task_revision_id
                      AND r.decision = 'approve'
                      AND r.author_user_id IS NOT NULL
                      AND r.reviewer_role_binding_id IS NOT NULL
                      AND r.reviewer_user_id <> r.author_user_id
                      AND r.admission_run_id = (
                          SELECT ar.id FROM task_admission_run ar
                          WHERE ar.task_revision_id = NEW.task_revision_id
                          ORDER BY ar.created_at DESC, ar.id DESC LIMIT 1
                      )
                )
            ) THEN
                RAISE EXCEPTION 'admitted state requires an approving independent review with known authorship'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            IF NEW.status = 'rejected' AND NOT EXISTS (
                SELECT 1 FROM task_admission_review r
                WHERE r.task_revision_id = NEW.task_revision_id
                  AND r.decision = 'reject'
                  AND r.author_user_id IS NOT NULL
                  AND r.reviewer_role_binding_id IS NOT NULL
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


def downgrade() -> None:
    # Restore the pre-h1 trigger bodies before removing the snapshot columns.
    # This keeps a downgrade to f0a1b2c3d4e5 executable, rather than leaving
    # functions that reference columns which no longer exist.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION aieb_guard_release_review_actor_state() RETURNS trigger AS $$
        DECLARE expected_subject uuid; target_state text; reviewer_authorized boolean;
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'independent release reviews are append-only'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            IF NEW.target_type = 'campaign_approval' THEN
                SELECT created_by_user_id, state INTO expected_subject, target_state
                  FROM campaign WHERE id = NEW.target_id FOR UPDATE;
            ELSIF NEW.target_type = 'publication_preparation' THEN
                SELECT prepared_by_user_id, status INTO expected_subject, target_state
                  FROM publication_preparation WHERE id = NEW.target_id FOR UPDATE;
            ELSE
                RAISE EXCEPTION 'unsupported independent review target %', NEW.target_type
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            IF (NEW.target_type = 'campaign_approval' AND target_state IS DISTINCT FROM 'planned')
               OR (NEW.target_type = 'publication_preparation' AND target_state IS DISTINCT FROM 'prepared') THEN
                RAISE EXCEPTION 'independent review target must be at its review gate'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            IF expected_subject IS NULL OR expected_subject <> NEW.subject_user_id THEN
                RAISE EXCEPTION 'independent review subject does not match target owner'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            SELECT EXISTS (
                SELECT 1 FROM role_bindings rb
                WHERE rb.user_id = NEW.reviewer_user_id
                  AND rb.role IN ('reviewer','administrator')
                  AND rb.scope = 'global'
            ) INTO reviewer_authorized;
            IF NOT reviewer_authorized THEN
                RAISE EXCEPTION 'independent review requires a global reviewer or administrator role'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE OR REPLACE FUNCTION aieb_guard_task_review_actor() RETURNS trigger AS $$
        DECLARE reviewer_authorized boolean;
        BEGIN
            IF TG_OP <> 'INSERT' THEN RETURN NEW; END IF;
            SELECT EXISTS (
                SELECT 1 FROM role_bindings rb
                WHERE rb.user_id = NEW.reviewer_user_id
                  AND rb.role IN ('reviewer','administrator')
                  AND rb.scope = 'global'
            ) INTO reviewer_authorized;
            IF NOT reviewer_authorized THEN
                RAISE EXCEPTION 'task admission review requires a global reviewer or administrator role'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE OR REPLACE FUNCTION aieb_guard_admission_review() RETURNS trigger AS $$
        DECLARE run_row task_admission_run%ROWTYPE; state_row task_admission_state%ROWTYPE;
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
            IF NEW.status = OLD.status THEN RETURN NEW; END IF;
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
    op.execute("DROP TRIGGER IF EXISTS reviewer_role_binding_history_guard ON role_bindings")
    op.execute("DROP FUNCTION IF EXISTS aieb_guard_reviewer_role_binding_history()")
    op.drop_index("ix_independent_review_role_binding", table_name="independent_review")
    op.drop_constraint("fk_independent_review_role_binding", "independent_review", type_="foreignkey")
    op.drop_column("independent_review", "reviewer_role_binding_id")
    op.drop_index("ix_task_admission_review_role_binding", table_name="task_admission_review")
    op.drop_constraint("fk_task_admission_review_role_binding", "task_admission_review", type_="foreignkey")
    op.drop_column("task_admission_review", "reviewer_role_binding_id")
