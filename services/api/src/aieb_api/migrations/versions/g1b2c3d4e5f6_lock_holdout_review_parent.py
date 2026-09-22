"""Serialize holdout reviews with manifest freeze/retirement.

The original review trigger checked the parent status without locking it.  A
separate trigger now takes the parent row lock before an insert, so a review
cannot commit after a concurrent freeze has already made the manifest
immutable.
"""
from __future__ import annotations

from alembic import op

revision = "g1b2c3d4e5f6"
down_revision = "f0a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION aieb_guard_holdout_review_parent() RETURNS trigger AS $$
        DECLARE manifest_status text;
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RETURN NEW;
            END IF;
            SELECT status INTO manifest_status
              FROM holdout_manifest
             WHERE id = NEW.holdout_manifest_id
             FOR UPDATE;
            IF manifest_status IS NULL THEN
                RAISE EXCEPTION 'holdout manifest does not exist'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            IF manifest_status IN ('frozen','retired') THEN
                RAISE EXCEPTION 'frozen or retired holdout manifests cannot receive reviews'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER holdout_review_parent_lock
        BEFORE INSERT ON holdout_review
        FOR EACH ROW EXECUTE FUNCTION aieb_guard_holdout_review_parent();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS holdout_review_parent_lock ON holdout_review")
    op.execute("DROP FUNCTION IF EXISTS aieb_guard_holdout_review_parent()")
