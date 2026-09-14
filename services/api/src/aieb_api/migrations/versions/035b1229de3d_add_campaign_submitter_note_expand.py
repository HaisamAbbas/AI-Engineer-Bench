"""add campaign submitter note (expand)

Revision ID: 035b1229de3d
Revises: 473c49fa55c7
Create Date: 2026-09-14 13:25:17.973244

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '035b1229de3d'
down_revision = '473c49fa55c7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Expand phase: additive, nullable, default-safe column. Compatible reads/writes,
    # backfill, and constraint enforcement are separate later migrations (spec section 44).
    op.add_column("campaign", sa.Column("submitter_note", sa.String(length=1024), nullable=True))


def downgrade() -> None:
    op.drop_column("campaign", "submitter_note")
