"""Harden direct-SQL holdout identity and audit checks.

The service validates these fields before writes, but the holdout boundary is
also expected to fail closed for an operator who can write the database
directly.  Keep this additive migration separate from the original registry
migration so already-applied environments never need a published migration
rewritten in place.
"""
from __future__ import annotations

from alembic import op

revision = "e8f9a0b1c2d3"
down_revision = "e7f8a9b0c1d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_holdout_manifest_identity_digest",
        "holdout_manifest",
        "manifest_digest ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        "ck_holdout_manifest_protocol_scope",
        "holdout_manifest",
        "length(btrim(protocol_version)) > 0 AND length(btrim(access_scope)) > 0",
    )
    op.create_check_constraint(
        "ck_holdout_review_evidence_digest",
        "holdout_review",
        "evidence_digest ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        "ck_holdout_review_reason",
        "holdout_review",
        "length(btrim(reason)) > 0",
    )
    op.create_check_constraint(
        "ck_holdout_review_report_size",
        "holdout_review",
        "octet_length(report::text) <= 65536",
    )
    op.create_check_constraint(
        "ck_holdout_access_actor",
        "holdout_access_audit",
        "length(btrim(actor_identity)) > 0",
    )
    op.create_check_constraint(
        "ck_holdout_access_digest",
        "holdout_access_audit",
        "object_digest ~ '^[0-9a-f]{64}$'",
    )


def downgrade() -> None:
    op.drop_constraint("ck_holdout_access_digest", "holdout_access_audit", type_="check")
    op.drop_constraint("ck_holdout_access_actor", "holdout_access_audit", type_="check")
    op.drop_constraint("ck_holdout_review_report_size", "holdout_review", type_="check")
    op.drop_constraint("ck_holdout_review_reason", "holdout_review", type_="check")
    op.drop_constraint("ck_holdout_review_evidence_digest", "holdout_review", type_="check")
    op.drop_constraint("ck_holdout_manifest_protocol_scope", "holdout_manifest", type_="check")
    op.drop_constraint("ck_holdout_manifest_identity_digest", "holdout_manifest", type_="check")
