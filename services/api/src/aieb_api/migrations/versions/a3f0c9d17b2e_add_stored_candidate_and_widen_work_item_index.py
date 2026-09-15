"""add candidate.stored_candidate and widen the work_item ready-lease index

ENG015-007: independently-leased verification needs to reconstruct the full
candidate (manifest plus every changed file's artifact-store reference) from
persistence alone - possibly in a different process, possibly a different
worker than the one that ran engineering, possibly after that one crashed.
Only tree_digest/manifest_digest were persisted before, which identify a
candidate but cannot reconstruct one. The ready-lease index also gains
`type` as its leading column now that two work-item types are claimed from
concurrently (engineering and verification).

Revision ID: a3f0c9d17b2e
Revises: 9e2f7a1c6b3d
Create Date: 2026-09-16 09:00:00.000000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = 'a3f0c9d17b2e'
down_revision = '9e2f7a1c6b3d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'candidate',
        sa.Column('stored_candidate', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='{}'),
    )
    op.drop_index('ix_work_item_ready_lease', table_name='work_item')
    op.create_index(
        'ix_work_item_ready_lease', 'work_item', ['type', 'state', 'lease_expiry'],
        unique=False, postgresql_where=sa.text("state = 'ready'"),
    )


def downgrade() -> None:
    op.drop_index('ix_work_item_ready_lease', table_name='work_item')
    op.create_index(
        'ix_work_item_ready_lease', 'work_item', ['state', 'lease_expiry'],
        unique=False, postgresql_where=sa.text("state = 'ready'"),
    )
    op.drop_column('candidate', 'stored_candidate')
