"""enforce publication snapshot immutability with a trigger

Review finding #14: nothing below the API route layer stopped a direct
UPDATE to a publication's snapshot/snapshot_digest columns, so a corrupted
or hand-edited row would be served as canonical public results with no
integrity check catching the mismatch. This mirrors the AUDIT-002 (#13)
frozen-revision triggers: status/supersedes_id may still change (publish ->
withdraw/supersede per spec sections 33/36), but the snapshot itself and
its digest are fixed at insert time.

Revision ID: 9e2f7a1c6b3d
Revises: 451c0d4b045e
Create Date: 2026-09-15 15:40:00.000000

"""
from __future__ import annotations

from alembic import op


revision = '9e2f7a1c6b3d'
down_revision = '451c0d4b045e'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION aieb_reject_publication_snapshot_mutation() RETURNS trigger AS $$
        BEGIN
            IF NEW.snapshot IS DISTINCT FROM OLD.snapshot OR NEW.snapshot_digest IS DISTINCT FROM OLD.snapshot_digest THEN
                RAISE EXCEPTION 'publication % snapshot is immutable; create a new publication instead', OLD.id
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER publication_snapshot_immutable
        BEFORE UPDATE ON publication
        FOR EACH ROW EXECUTE FUNCTION aieb_reject_publication_snapshot_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS publication_snapshot_immutable ON publication;")
    op.execute("DROP FUNCTION IF EXISTS aieb_reject_publication_snapshot_mutation();")
