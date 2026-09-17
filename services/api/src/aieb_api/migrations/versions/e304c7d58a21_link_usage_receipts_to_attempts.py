"""Link usage receipts to runs for Run Evidence usage reporting.

Revision ID: e304c7d58a21
Revises: d784e4ac6190
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "e304c7d58a21"
down_revision = "d784e4ac6190"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("usage_request", sa.Column("attempt_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_usage_request_attempt_id_attempt", "usage_request", "attempt", ["attempt_id"], ["id"])
    op.create_index("ix_usage_request_attempt", "usage_request", ["attempt_id"])


def downgrade() -> None:
    op.drop_index("ix_usage_request_attempt", table_name="usage_request")
    op.drop_constraint("fk_usage_request_attempt_id_attempt", "usage_request", type_="foreignkey")
    op.drop_column("usage_request", "attempt_id")
