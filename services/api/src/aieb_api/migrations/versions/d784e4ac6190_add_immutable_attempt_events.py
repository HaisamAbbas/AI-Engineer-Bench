"""Persist append-only attempt lifecycle trace events.

Revision ID: d784e4ac6190
Revises: c8d41a6e2b90
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "d784e4ac6190"
down_revision = "c8d41a6e2b90"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "attempt_event",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["attempt_id"], ["attempt.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("attempt_id", "sequence", name="uq_attempt_event_sequence"),
    )
    op.create_index("ix_attempt_event_chronology", "attempt_event", ["attempt_id", "sequence"])
    op.execute("""
        CREATE FUNCTION aieb_reject_attempt_event_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'attempt event % is append-only', OLD.id
                USING ERRCODE = 'integrity_constraint_violation';
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER attempt_event_immutable BEFORE UPDATE OR DELETE ON attempt_event
        FOR EACH ROW EXECUTE FUNCTION aieb_reject_attempt_event_mutation();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS attempt_event_immutable ON attempt_event")
    op.execute("DROP FUNCTION IF EXISTS aieb_reject_attempt_event_mutation()")
    op.drop_index("ix_attempt_event_chronology", table_name="attempt_event")
    op.drop_table("attempt_event")
