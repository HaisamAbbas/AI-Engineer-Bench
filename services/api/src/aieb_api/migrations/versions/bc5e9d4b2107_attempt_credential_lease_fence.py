"""ENG-020 (spec section 37), codex-audit finding 3: lease-fence scoped credentials.

Binds each attempt_credential's (re)issue to the WORK ITEM lease that issued it
(work_item_id, worker_id, lease_generation), so a stale/fenced worker can never issue
or rotate a current worker's credential, and the audit can attribute any token to the
lease that minted it. Columns are NULLable: rows predating this migration carried no
fence record and remain inspectable, but no live credential is issued without one from
here on (the repository rejects fence-less issuance).

Revision ID: bc5e9d4b2107
Revises: ba47e9c84511
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "bc5e9d4b2107"
down_revision = "ba47e9c84511"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("attempt_credential", sa.Column("work_item_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("work_item.id"), nullable=True))
    op.add_column("attempt_credential", sa.Column("worker_id", sa.String(length=128), nullable=True))
    op.add_column("attempt_credential", sa.Column("lease_generation", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("attempt_credential", "lease_generation")
    op.drop_column("attempt_credential", "worker_id")
    op.drop_column("attempt_credential", "work_item_id")