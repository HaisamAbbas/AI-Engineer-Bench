"""Enforce reviewer authorization and target state at the DB boundary.

The API already resolves server-side roles and checks target state.  These
separate triggers prevent a direct SQL writer from manufacturing an
independent review with a visitor/operator identity or attaching it to a
campaign/publication that is no longer at the review gate.
"""
from __future__ import annotations

from alembic import op

revision = "f0a1b2c3d4e5"
down_revision = "e8f9a0b1c2d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION aieb_guard_release_review_actor_state() RETURNS trigger AS $$
        DECLARE expected_subject uuid; target_state text; reviewer_authorized boolean;
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RETURN NEW;
            END IF;
            IF NEW.target_type = 'campaign_approval' THEN
                SELECT created_by_user_id, state INTO expected_subject, target_state
                  FROM campaign WHERE id = NEW.target_id;
                IF target_state IS DISTINCT FROM 'planned' THEN
                    RAISE EXCEPTION 'campaign review target must be planned, not %', target_state
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
            ELSIF NEW.target_type = 'publication_preparation' THEN
                SELECT prepared_by_user_id, status INTO expected_subject, target_state
                  FROM publication_preparation WHERE id = NEW.target_id;
                IF target_state IS DISTINCT FROM 'prepared' THEN
                    RAISE EXCEPTION 'publication review target must be prepared, not %', target_state
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
            ELSE
                RAISE EXCEPTION 'unsupported independent review target %', NEW.target_type
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
        CREATE TRIGGER independent_review_actor_state_guard
        BEFORE INSERT ON independent_review
        FOR EACH ROW EXECUTE FUNCTION aieb_guard_release_review_actor_state();
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION aieb_guard_task_review_actor() RETURNS trigger AS $$
        DECLARE reviewer_authorized boolean;
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RETURN NEW;
            END IF;
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
        CREATE TRIGGER task_admission_review_actor_guard
        BEFORE INSERT ON task_admission_review
        FOR EACH ROW EXECUTE FUNCTION aieb_guard_task_review_actor();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS task_admission_review_actor_guard ON task_admission_review")
    op.execute("DROP FUNCTION IF EXISTS aieb_guard_task_review_actor()")
    op.execute("DROP TRIGGER IF EXISTS independent_review_actor_state_guard ON independent_review")
    op.execute("DROP FUNCTION IF EXISTS aieb_guard_release_review_actor_state()")
