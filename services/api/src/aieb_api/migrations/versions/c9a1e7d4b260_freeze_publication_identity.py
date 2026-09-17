"""Freeze publication identity without changing lifecycle transitions.

Revision ID: c9a1e7d4b260
Revises: b7e4a9c2d1f8
"""
from alembic import op

revision = "c9a1e7d4b260"
down_revision = "b7e4a9c2d1f8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION aieb_freeze_publication_identity() RETURNS trigger AS $$
        BEGIN
            IF NEW.id IS DISTINCT FROM OLD.id
               OR NEW.campaign_id IS DISTINCT FROM OLD.campaign_id
               OR NEW.created_at IS DISTINCT FROM OLD.created_at
               OR NEW.publication_class IS DISTINCT FROM OLD.publication_class THEN
                RAISE EXCEPTION 'publication identity and class are immutable'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            IF OLD.status IN ('withdrawn', 'superseded') AND NEW.status IS DISTINCT FROM OLD.status THEN
                RAISE EXCEPTION 'terminal publication status is immutable'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER freeze_publication_identity BEFORE UPDATE ON publication
        FOR EACH ROW EXECUTE FUNCTION aieb_freeze_publication_identity();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER freeze_publication_identity ON publication")
    op.execute("DROP FUNCTION aieb_freeze_publication_identity()")
