"""persist worker observability counters and exact heartbeat timestamps"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "c7d8e9f0a1b2"
# The repository already had two independently-developed heads (publication
# and worker-fencing). This migration deliberately merges both so a normal
# `upgrade head` has one deterministic target.
down_revision = ("f5a2c1d9e7b4", "d5a3f7b9c1e2")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("work_item", sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    # Existing live leases predate the exact timestamp. Backfill conservatively
    # using the historical default and let all new claims/heartbeats be exact.
    op.execute(
        sa.text(
            "UPDATE work_item SET last_heartbeat_at = lease_expiry - INTERVAL '60 seconds' "
            "WHERE state = 'leased' AND lease_expiry IS NOT NULL"
        )
    )
    op.create_table(
        "metric_counter",
        sa.Column("name", sa.String(length=128), primary_key=True),
        sa.Column("value", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("metric_counter")
    op.drop_column("work_item", "last_heartbeat_at")
