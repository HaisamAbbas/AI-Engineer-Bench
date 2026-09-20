"""Add attempt_model_identity: per-attempt/role requested vs reported model
identity and settings/coverage disclosure, closing the ENG-023 review gap
that requested/reported identity was not persisted into the authoritative
usage ledger (only serialized into model_track_summary.json).

Revision ID: b3f1c2a9d4e7
Revises: c7d8e9f0a1b2
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b3f1c2a9d4e7"
down_revision = "c7d8e9f0a1b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "attempt_model_identity",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("attempt.id"), nullable=True),
        sa.Column("actor_role", sa.String(32), nullable=False),
        sa.Column("requested_model", sa.String(256), nullable=False),
        sa.Column("reported_model", sa.String(256), nullable=True),
        sa.Column("settings_digest", sa.String(64), nullable=True),
        sa.Column("coverage_label", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "actor_role in ('engineer','dev_application','verifier_application','verifier_judge')",
            name="ck_attempt_model_identity_actor_role",
        ),
        sa.UniqueConstraint("attempt_id", "actor_role", name="uq_attempt_model_identity_attempt_role"),
    )
    op.create_index("ix_attempt_model_identity_attempt", "attempt_model_identity", ["attempt_id"])


def downgrade() -> None:
    op.drop_index("ix_attempt_model_identity_attempt", table_name="attempt_model_identity")
    op.drop_table("attempt_model_identity")
