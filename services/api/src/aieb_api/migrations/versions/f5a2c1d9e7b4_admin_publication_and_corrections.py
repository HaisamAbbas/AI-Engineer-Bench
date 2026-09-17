"""admin, publication and corrections (ENG-017 + ENG-018)

Adds the write-path schema for campaign administration + budgets (ENG-017) and
publication/review/redaction/corrections (ENG-018):

- campaign.created_by_user_id (reject self-approval of one's own campaign);
- budget_reservation (honest ESTIMATED_TIME_LIMITED reservation - no provider
  hard hold exists yet);
- correction_run + evaluation.correction_run_id (full re-evaluation regrade;
  corrected evaluations carry a distinct identity and are retained alongside
  originals);
- publication_preparation (prepare -> review -> publish, keeping PublicationRow
  strictly immutable and inserted only at publish time);
- publication signing columns, review_kind, reason; the publication
  immutability trigger is extended to also freeze the signed manifest while
  still allowing status/reason transitions (withdraw/supersede).

Revision ID: f5a2c1d9e7b4
Revises: e304c7d58a21
Create Date: 2026-09-17 12:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "f5a2c1d9e7b4"
down_revision = "e304c7d58a21"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("campaign", sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_campaign_created_by_user", "campaign", "users", ["created_by_user_id"], ["id"]
    )

    op.create_table(
        "budget_reservation",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("campaign.id"), nullable=False),
        sa.Column("reservation_id", sa.String(length=64), nullable=False),
        sa.Column("enforcement", sa.String(length=32), nullable=False),
        sa.Column("reserved_usd", sa.Numeric(20, 6), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("campaign_id", name="uq_budget_reservation_campaign"),
        sa.CheckConstraint("enforcement in ('hard','estimated_time_limited')", name="ck_budget_reservation_enforcement"),
        sa.CheckConstraint("status in ('active','released','consumed')", name="ck_budget_reservation_status"),
    )

    op.create_table(
        "correction_run",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("campaign.id"), nullable=False),
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("corrected_evaluator_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("evaluator_revision.id"), nullable=False),
        sa.Column("corrected_fixture_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("fixture_revision.id"), nullable=False),
        sa.Column("scoring_correction_digest", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="running"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status in ('running','completed','failed')", name="ck_correction_run_status"),
    )
    op.create_index("ix_correction_run_campaign", "correction_run", ["campaign_id"])

    op.add_column("evaluation", sa.Column("correction_run_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_evaluation_correction_run", "evaluation", "correction_run", ["correction_run_id"], ["id"]
    )

    op.create_table(
        "publication_preparation",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("campaign.id"), nullable=False),
        sa.Column("prepared_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("snapshot_digest", sa.String(length=64), nullable=False),
        sa.Column("evidence_manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("evidence_manifest_digest", sa.String(length=64), nullable=False),
        sa.Column("review_kind", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="prepared"),
        sa.Column("supersedes_publication_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("publication.id"), nullable=True),
        sa.Column("correction_reason", sa.Text(), nullable=True),
        sa.Column("published_publication_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("publication.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status in ('prepared','approved','rejected','published')", name="ck_publication_preparation_status"),
        sa.CheckConstraint("review_kind is null or review_kind in ('single_maintainer','independent')", name="ck_publication_preparation_review_kind"),
    )
    op.create_index("ix_publication_preparation_campaign", "publication_preparation", ["campaign_id"])

    op.add_column("publication", sa.Column("manifest_signature", sa.Text(), nullable=True))
    op.add_column("publication", sa.Column("signing_public_key", sa.Text(), nullable=True))
    op.add_column("publication", sa.Column("signing_key_id", sa.String(length=64), nullable=True))
    op.add_column("publication", sa.Column("signed_manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("publication", sa.Column("review_kind", sa.String(length=32), nullable=True))
    op.add_column("publication", sa.Column("reason", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_publication_review_kind", "publication",
        "review_kind is null or review_kind in ('single_maintainer','independent')",
    )

    # Extend the publication immutability trigger: also freeze the signed
    # manifest columns (set once at insert), while STILL allowing status,
    # reason, review_kind and supersedes_id to transition (withdraw/supersede).
    op.execute("""
        CREATE OR REPLACE FUNCTION aieb_reject_publication_snapshot_mutation() RETURNS trigger AS $$
        BEGIN
            IF NEW.snapshot IS DISTINCT FROM OLD.snapshot OR NEW.snapshot_digest IS DISTINCT FROM OLD.snapshot_digest
               OR NEW.evidence_manifest IS DISTINCT FROM OLD.evidence_manifest
               OR NEW.evidence_manifest_digest IS DISTINCT FROM OLD.evidence_manifest_digest
               OR NEW.manifest_signature IS DISTINCT FROM OLD.manifest_signature
               OR NEW.signing_public_key IS DISTINCT FROM OLD.signing_public_key
               OR NEW.signing_key_id IS DISTINCT FROM OLD.signing_key_id
               OR NEW.signed_manifest IS DISTINCT FROM OLD.signed_manifest THEN
                RAISE EXCEPTION 'publication % snapshot and signed manifest are immutable; create a new publication instead', OLD.id
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)


def downgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION aieb_reject_publication_snapshot_mutation() RETURNS trigger AS $$
        BEGIN
            IF NEW.snapshot IS DISTINCT FROM OLD.snapshot OR NEW.snapshot_digest IS DISTINCT FROM OLD.snapshot_digest
               OR NEW.evidence_manifest IS DISTINCT FROM OLD.evidence_manifest
               OR NEW.evidence_manifest_digest IS DISTINCT FROM OLD.evidence_manifest_digest THEN
                RAISE EXCEPTION 'publication % snapshot and evidence manifest are immutable; create a new publication instead', OLD.id
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.drop_constraint("ck_publication_review_kind", "publication", type_="check")
    for column in ("reason", "review_kind", "signed_manifest", "signing_key_id", "signing_public_key", "manifest_signature"):
        op.drop_column("publication", column)
    op.drop_index("ix_publication_preparation_campaign", table_name="publication_preparation")
    op.drop_table("publication_preparation")
    op.drop_constraint("fk_evaluation_correction_run", "evaluation", type_="foreignkey")
    op.drop_column("evaluation", "correction_run_id")
    op.drop_index("ix_correction_run_campaign", table_name="correction_run")
    op.drop_table("correction_run")
    op.drop_table("budget_reservation")
    op.drop_constraint("fk_campaign_created_by_user", "campaign", type_="foreignkey")
    op.drop_column("campaign", "created_by_user_id")
