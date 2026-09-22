"""V2-GAP-004 orchestration state machine and exact campaign matrix

Adds the plan's orchestration states (planned, approved) between frozen and
running, the plan-time materialized matrix identity (campaign.matrix_digest),
the per-cell expansion fields the plan requires CampaignCell rows to persist
(order, budget allocation, planned deadline, initial status), the auditable
budget-reservation provenance (formula + budget-profile digest + authorized
cap), and a PostgreSQL-level state-transition guard so illegal transitions
are rejected in the database itself, not only in the service layer
(aieb_api.orchestration.ALLOWED_TRANSITIONS is the service mirror).

Revision ID: b9c0d1e2f3a4
Revises: 6f2a9d5c1e73
Create Date: 2026-09-22

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = 'b9c0d1e2f3a4'
down_revision = '6f2a9d5c1e73'
branch_labels = None
depends_on = None

_OLD_CAMPAIGN_STATES = "('draft','frozen','running','paused','cancelling','cancelled','completed','incomplete')"
_NEW_CAMPAIGN_STATES = "('draft','frozen','planned','approved','running','paused','cancelling','cancelled','completed','incomplete')"

# Service mirror: aieb_api.orchestration.ALLOWED_TRANSITIONS. A same-state
# update is bookkeeping, not a transition, and stays allowed.
_TRANSITION_FUNCTION = """
CREATE OR REPLACE FUNCTION aieb_reject_illegal_campaign_transition() RETURNS trigger AS $$
DECLARE
    allowed text[];
BEGIN
    IF NEW.state IS NOT DISTINCT FROM OLD.state THEN
        RETURN NEW;
    END IF;
    allowed := CASE OLD.state
        WHEN 'draft'      THEN ARRAY['frozen']
        WHEN 'frozen'     THEN ARRAY['planned', 'cancelling']
        WHEN 'planned'    THEN ARRAY['approved', 'cancelling']
        WHEN 'approved'   THEN ARRAY['running', 'cancelling']
        WHEN 'running'    THEN ARRAY['paused', 'cancelling', 'completed', 'incomplete']
        WHEN 'paused'     THEN ARRAY['running', 'cancelling', 'completed', 'incomplete']
        WHEN 'cancelling' THEN ARRAY['cancelled']
        ELSE ARRAY[]::text[]
    END;
    IF NOT (NEW.state = ANY(allowed)) THEN
        RAISE EXCEPTION 'illegal campaign state transition: % -> % (campaign %)', OLD.state, NEW.state, OLD.id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.drop_constraint('ck_campaign_state', 'campaign', type_='check')
    op.create_check_constraint('ck_campaign_state', 'campaign', f"state in {_NEW_CAMPAIGN_STATES}")

    op.add_column('campaign', sa.Column('matrix_digest', sa.String(64), nullable=True))

    op.add_column('trial', sa.Column('order_index', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('trial', sa.Column('budget_allocation_usd', sa.Numeric(20, 6), nullable=True))
    op.add_column('trial', sa.Column('planned_deadline_seconds', sa.Integer(), nullable=True))
    op.add_column('trial', sa.Column('status', sa.String(16), nullable=False, server_default='planned'))
    op.create_check_constraint('ck_trial_status', 'trial', "status in ('planned','enqueued')")

    op.add_column('budget_reservation', sa.Column('reservation_formula', postgresql.JSONB(none_as_null=True), nullable=True))
    op.add_column('budget_reservation', sa.Column('budget_profile_digest', sa.String(64), nullable=True))
    op.add_column('budget_reservation', sa.Column('authorized_cap_usd', sa.Numeric(20, 6), nullable=True))

    op.execute(_TRANSITION_FUNCTION)
    op.execute(
        """
        CREATE TRIGGER campaign_state_transition
        BEFORE UPDATE ON campaign
        FOR EACH ROW EXECUTE FUNCTION aieb_reject_illegal_campaign_transition();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS campaign_state_transition ON campaign;")
    op.execute("DROP FUNCTION IF EXISTS aieb_reject_illegal_campaign_transition();")

    op.drop_column('budget_reservation', 'authorized_cap_usd')
    op.drop_column('budget_reservation', 'budget_profile_digest')
    op.drop_column('budget_reservation', 'reservation_formula')

    op.drop_constraint('ck_trial_status', 'trial', type_='check')
    op.drop_column('trial', 'status')
    op.drop_column('trial', 'planned_deadline_seconds')
    op.drop_column('trial', 'budget_allocation_usd')
    op.drop_column('trial', 'order_index')

    op.drop_column('campaign', 'matrix_digest')

    op.drop_constraint('ck_campaign_state', 'campaign', type_='check')
    op.create_check_constraint('ck_campaign_state', 'campaign', f"state in {_OLD_CAMPAIGN_STATES}")
