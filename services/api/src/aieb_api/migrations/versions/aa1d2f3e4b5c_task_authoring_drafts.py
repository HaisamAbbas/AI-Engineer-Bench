"""add maintainer task drafts"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "aa1d2f3e4b5c"
down_revision = "b3f1c2a9d4e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_draft",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("slug", sa.String(128), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("manifest", postgresql.JSONB(), nullable=False),
        sa.Column("ticket_text", sa.Text(), nullable=False),
        sa.Column("evaluator_code_digest", sa.String(64), nullable=False),
        sa.Column("evaluator_contract_version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("created_by_user_id", sa.UUID(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("frozen_revision_id", sa.UUID(), sa.ForeignKey("task_revision.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status in ('draft','frozen')", name="ck_task_draft_status"),
        sa.UniqueConstraint("slug", "version", name="uq_task_draft_slug_version"),
    )


def downgrade() -> None:
    op.drop_table("task_draft")
