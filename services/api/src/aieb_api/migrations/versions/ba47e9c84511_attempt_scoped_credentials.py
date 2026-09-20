"""ENG-020 (spec section 37): per-attempt, per-role scoped credentials.

A candidate-role and a verifier-role credential per attempt, each stored only as a
sha256 hash with an expiry and a revocation timestamp. One row per (attempt_id,
actor_role) - re-issuing with a fresh token rotates the hash rather than piling up
rows per attempt.

Revision ID: ba47e9c84511
Revises: d3f6a8e21c94
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "ba47e9c84511"
down_revision = "d3f6a8e21c94"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "attempt_credential",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("attempt.id"), nullable=False),
        sa.Column("actor_role", sa.String(length=32), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("attempt_id", "actor_role", name="uq_attempt_credential_attempt_role"),
        sa.CheckConstraint("actor_role in ('candidate','verifier')", name="ck_attempt_credential_actor_role"),
    )


def downgrade() -> None:
    op.drop_table("attempt_credential")