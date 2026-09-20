"""ENG-020 restore-drill fencing (gap 4): a monotonic system fence epoch.

After a database restore from backup, the restored snapshot still shows every
pre-restore lease and credential exactly as it was - a lease whose expiry is
still in the future is indistinguishable from a live one until the reconciler's
expiry sweep fires (and if a stale worker keeps heartbeating it, never). This
migration adds the mechanism that closes that hole:

- a singleton `system_fence` row carrying the CURRENT fence epoch (seeded at 0,
  only ever advanced, by the operator, immediately after a restore);
- `work_item.lease_epoch` - the epoch a lease was claimed under (existing rows
  backfilled to 0);
- `attempt_credential.lease_epoch` - the epoch a credential was (re)issued
  under (existing rows backfilled to 0).

Every fenced lease/credential operation requires the stored epoch to equal the
current fence epoch, so advancing the epoch after a restore orphans ALL
pre-restore leases and credentials at first touch - before reconciliation - and
the reconciler sweeps stale-epoch leases on its next poll even while their
expiry is still in the future.

Revision ID: d5a3f7b9c1e2
Revises: bc5e9d4b2107
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "d5a3f7b9c1e2"
down_revision = "bc5e9d4b2107"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "system_fence",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("lease_fence_epoch", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reason", sa.String(length=512), nullable=True),
        sa.Column("activated_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_system_fence_singleton"),
    )
    # One singleton row, epoch 0 - the epoch every existing (backfilled) lease matches.
    op.execute("INSERT INTO system_fence (id, lease_fence_epoch) VALUES (1, 0)")
    op.add_column("work_item", sa.Column("lease_epoch", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("attempt_credential", sa.Column("lease_epoch", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("attempt_credential", "lease_epoch")
    op.drop_column("work_item", "lease_epoch")
    op.drop_table("system_fence")