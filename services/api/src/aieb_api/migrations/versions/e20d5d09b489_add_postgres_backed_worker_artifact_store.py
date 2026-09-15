"""add Postgres-backed worker artifact store

ENG-015 review finding #5: "the topology claim is more honest, but the
hosted storage requirement remains unimplemented" - candidate bytes lived
only in a worker-local FilesystemArtifactStore, so "a different worker can
recover a candidate from the database alone" was only true if every worker
happened to share a filesystem mount, which was not the actual default.
Adds worker_artifact_blob (content-addressed bytes) and
worker_artifact_reference (access-controlled references to them) so the
hosted worker's PostgresArtifactStore can make shared storage genuinely the
default, using the same database every worker already connects to.

Revision ID: e20d5d09b489
Revises: a3f0c9d17b2e
Create Date: 2026-09-16 12:00:00.000000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = 'e20d5d09b489'
down_revision = 'a3f0c9d17b2e'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'worker_artifact_blob',
        sa.Column('sha256', sa.String(length=64), primary_key=True),
        sa.Column('byte_length', sa.Integer(), nullable=False),
        sa.Column('data', sa.LargeBinary(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        'worker_artifact_reference',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('blob_sha256', sa.String(length=64), sa.ForeignKey('worker_artifact_blob.sha256'), nullable=False),
        sa.Column('access_scope', sa.String(length=128), nullable=False),
        sa.Column('visibility', sa.String(length=16), nullable=False, server_default='restricted'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("visibility in ('public','restricted')", name='ck_worker_artifact_reference_visibility'),
    )


def downgrade() -> None:
    op.drop_table('worker_artifact_reference')
    op.drop_table('worker_artifact_blob')
