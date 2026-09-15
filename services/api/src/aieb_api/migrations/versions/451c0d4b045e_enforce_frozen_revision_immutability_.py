"""enforce frozen revision immutability with triggers

Prompt 11 (ENG-014) required immutable frozen revisions to be enforced "in
persistence, not only in UI checks" - the API previously relied entirely on
simply not exposing an update route. A direct UPDATE against these tables
(a bug, a future route, or a hand-run migration) had nothing to stop it.

Revision ID: 451c0d4b045e
Revises: 786858c32677
Create Date: 2026-09-15 11:20:41.131381

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '451c0d4b045e'
down_revision = '786858c32677'
branch_labels = None
depends_on = None

_IMMUTABLE_TABLES = ("task_revision", "evaluator_revision", "entrant_revision", "fixture_revision")


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION aieb_reject_revision_update() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'revision % on % is immutable; create a new revision row instead', OLD.id, TG_TABLE_NAME
                USING ERRCODE = 'integrity_constraint_violation';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table in _IMMUTABLE_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER {table}_immutable
            BEFORE UPDATE ON {table}
            FOR EACH ROW EXECUTE FUNCTION aieb_reject_revision_update();
            """
        )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION aieb_reject_frozen_campaign_mutation() RETURNS trigger AS $$
        BEGIN
            IF OLD.state <> 'draft' AND (
                NEW.draft IS DISTINCT FROM OLD.draft
                OR NEW.resolved IS DISTINCT FROM OLD.resolved
                OR NEW.manifest_digest IS DISTINCT FROM OLD.manifest_digest
                OR NEW.cohort_digest IS DISTINCT FROM OLD.cohort_digest
            ) THEN
                RAISE EXCEPTION 'campaign % is frozen; its draft/resolved manifest cannot change - create a new campaign instead', OLD.id
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER campaign_frozen_manifest_immutable
        BEFORE UPDATE ON campaign
        FOR EACH ROW EXECUTE FUNCTION aieb_reject_frozen_campaign_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS campaign_frozen_manifest_immutable ON campaign;")
    op.execute("DROP FUNCTION IF EXISTS aieb_reject_frozen_campaign_mutation();")
    for table in _IMMUTABLE_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table};")
    op.execute("DROP FUNCTION IF EXISTS aieb_reject_revision_update();")
