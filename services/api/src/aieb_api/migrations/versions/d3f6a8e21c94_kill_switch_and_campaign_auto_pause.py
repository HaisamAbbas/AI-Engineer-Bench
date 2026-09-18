"""ENG-020: global spend/dispatch kill switch and per-campaign auto-pause.

These are two distinct mechanisms (spec sections 39/48), not one: auto-pause is
per-campaign, scoped to whatever caused it, and requires operator review before
resume (a manual, informed decision - not an automatic clear). The kill switch
is a single global flag that stops ALL new dispatch platform-wide and requests
bounded teardown of active work, independent of any one campaign's health.

Revision ID: d3f6a8e21c94
Revises: c9a1e7d4b260
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "d3f6a8e21c94"
down_revision = "c9a1e7d4b260"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("campaign", sa.Column("consecutive_infrastructure_failures", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("campaign", sa.Column("auto_paused", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("campaign", sa.Column("auto_pause_reason", sa.String(length=256), nullable=True))

    op.create_table(
        "kill_switch",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("reason", sa.String(length=512), nullable=True),
        sa.Column("activated_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_kill_switch_singleton"),
    )
    # Seed the single row up front so `UPDATE kill_switch SET active = ...` always has a row
    # to affect - no INSERT-or-UPDATE race at the one place the kill switch is ever flipped.
    op.execute("INSERT INTO kill_switch (id, active) VALUES (1, false)")


def downgrade() -> None:
    op.drop_table("kill_switch")
    op.drop_column("campaign", "auto_pause_reason")
    op.drop_column("campaign", "auto_paused")
    op.drop_column("campaign", "consecutive_infrastructure_failures")
