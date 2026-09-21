"""add authored source metadata to task drafts"""
from __future__ import annotations
import sqlalchemy as sa
from alembic import op

revision = "bc2d3e4f5a6b"
down_revision = "aa1d2f3e4b5c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("task_draft", sa.Column("source_strategy", sa.String(32), nullable=False, server_default="authored"))
    op.add_column("task_draft", sa.Column("repository_url", sa.Text(), nullable=False, server_default="local://unspecified"))
    op.add_column("task_draft", sa.Column("source_revision", sa.String(128), nullable=False, server_default="unspecified"))
    op.add_column("task_draft", sa.Column("source_content_digest", sa.String(64), nullable=False, server_default="0" * 64))
    op.add_column("task_draft", sa.Column("source_license_id", sa.String(128), nullable=False, server_default="unspecified"))
    op.add_column("task_draft", sa.Column("source_provenance_digest", sa.String(64), nullable=False, server_default="0" * 64))


def downgrade() -> None:
    for name in ("source_provenance_digest", "source_license_id", "source_content_digest", "source_revision", "repository_url", "source_strategy"):
        op.drop_column("task_draft", name)
