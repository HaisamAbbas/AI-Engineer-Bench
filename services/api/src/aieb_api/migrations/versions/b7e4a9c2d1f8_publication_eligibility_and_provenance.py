"""publication eligibility, provenance freeze and distinct reasons (ENG-018)

Prompt 14 review follow-up:

- `publication.publication_class` + `publication_preparation.publication_class`
  (`ranked` vs `non_ranked`): a ranked publication must have complete cohort
  coverage; incomplete snapshots are only publishable as explicitly
  non-ranking publications and can never become the canonical ranked release.
- `publication.withdrawal_reason`: the withdrawal rationale is stored
  SEPARATELY from `reason` (the correction/supersession rationale), so a
  withdrawal can no longer overwrite a recorded correction reason.
- The publication immutability trigger now freezes ALL post-insert
  provenance: snapshot, evidence manifest, signed manifest columns, AND
  `review_kind`, `supersedes_id`, `reason`, `reviewer_id`. Only `status`,
  `published-at-preserving` withdrawal transitions may change, and
  `withdrawal_reason` may only be SET (never overwritten) when the status
  becomes `withdrawn`. Displayed provenance can no longer diverge from the
  signed manifest.

Revision ID: b7e4a9c2d1f8
Revises: f5a2c1d9e7b4
Create Date: 2026-09-18 10:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "b7e4a9c2d1f8"
down_revision = "f5a2c1d9e7b4"
branch_labels = None
depends_on = None


_PROVENANCE_FROZEN_TRIGGER = """
    CREATE OR REPLACE FUNCTION aieb_reject_publication_snapshot_mutation() RETURNS trigger AS $$
    BEGIN
        IF NEW.snapshot IS DISTINCT FROM OLD.snapshot OR NEW.snapshot_digest IS DISTINCT FROM OLD.snapshot_digest
           OR NEW.evidence_manifest IS DISTINCT FROM OLD.evidence_manifest
           OR NEW.evidence_manifest_digest IS DISTINCT FROM OLD.evidence_manifest_digest
           OR NEW.manifest_signature IS DISTINCT FROM OLD.manifest_signature
           OR NEW.signing_public_key IS DISTINCT FROM OLD.signing_public_key
           OR NEW.signing_key_id IS DISTINCT FROM OLD.signing_key_id
           OR NEW.signed_manifest IS DISTINCT FROM OLD.signed_manifest
           OR NEW.review_kind IS DISTINCT FROM OLD.review_kind
           OR NEW.supersedes_id IS DISTINCT FROM OLD.supersedes_id
           OR NEW.reason IS DISTINCT FROM OLD.reason
           OR NEW.reviewer_id IS DISTINCT FROM OLD.reviewer_id THEN
            RAISE EXCEPTION 'publication % provenance is immutable; create a new publication instead', OLD.id
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        -- The withdrawal reason is append-only: it may be SET when the
        -- publication first becomes `withdrawn`, and never altered or erased
        -- afterwards - a withdrawal cannot rewrite the correction reason or
        -- its own recorded rationale.
        IF NEW.withdrawal_reason IS DISTINCT FROM OLD.withdrawal_reason THEN
            IF NOT (OLD.status = 'published' AND NEW.status = 'withdrawn' AND OLD.withdrawal_reason IS NULL
                    AND NEW.withdrawal_reason IS NOT NULL) THEN
                RAISE EXCEPTION 'publication % withdrawal_reason is append-only at withdrawal', OLD.id
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.add_column(
        "publication",
        sa.Column("publication_class", sa.String(length=16), nullable=False, server_default="ranked"),
    )
    op.add_column("publication", sa.Column("withdrawal_reason", sa.Text(), nullable=True))
    op.add_column(
        "publication_preparation",
        sa.Column("publication_class", sa.String(length=16), nullable=False, server_default="ranked"),
    )
    op.create_check_constraint(
        "ck_publication_class", "publication",
        "publication_class in ('ranked','non_ranked')",
    )
    op.create_check_constraint(
        "ck_publication_preparation_class", "publication_preparation",
        "publication_class in ('ranked','non_ranked')",
    )
    # Idempotency scopes now carry the server-resolved principal suffix.
    op.alter_column("idempotency_record", "scope", existing_type=sa.String(length=128), type_=sa.String(length=256))
    op.execute(_PROVENANCE_FROZEN_TRIGGER)


def downgrade() -> None:
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
    op.alter_column("idempotency_record", "scope", existing_type=sa.String(length=256), type_=sa.String(length=128))
    op.drop_constraint("ck_publication_preparation_class", "publication_preparation", type_="check")
    op.drop_constraint("ck_publication_class", "publication", type_="check")
    op.drop_column("publication_preparation", "publication_class")
    op.drop_column("publication", "withdrawal_reason")
    op.drop_column("publication", "publication_class")