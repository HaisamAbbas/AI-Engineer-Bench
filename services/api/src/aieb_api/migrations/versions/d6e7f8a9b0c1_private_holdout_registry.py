"""V2-GAP-005 private holdout registry and access audit.

Only opaque private storage references and digests are persisted; fixture bytes
must remain in an access-controlled evaluator store outside the repository and
build context.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d6e7f8a9b0c1"
down_revision = "b9c0d1e2f3a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "holdout_manifest",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("task_revision_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("task_revision.id"), nullable=False),
        sa.Column("family_id", sa.String(128), nullable=False),
        sa.Column("evaluator_revision_digest", sa.String(64), nullable=False),
        sa.Column("storage_uri", sa.Text(), nullable=False),
        sa.Column("object_digest", sa.String(64), nullable=False),
        sa.Column("object_length", sa.BigInteger(), nullable=False),
        sa.Column("manifest_digest", sa.String(64), nullable=False),
        sa.Column("overlap_review_digest", sa.String(64), nullable=True),
        sa.Column("protocol_version", sa.String(64), nullable=False),
        sa.Column("access_scope", sa.String(256), nullable=False),
        sa.Column("split", sa.String(32), nullable=False, server_default="official"),
        sa.Column("retention_class", sa.String(32), nullable=False, server_default="official"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("frozen_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("supersedes_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("holdout_manifest.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status in ('draft','reviewed','frozen','retired')", name="ck_holdout_manifest_status"),
        sa.CheckConstraint("object_length >= 0", name="ck_holdout_manifest_length"),
        sa.CheckConstraint("object_digest ~ '^[0-9a-f]{64}$'", name="ck_holdout_manifest_digest"),
        sa.CheckConstraint("overlap_review_digest IS NULL OR overlap_review_digest ~ '^[0-9a-f]{64}$'", name="ck_holdout_manifest_overlap_digest"),
        sa.CheckConstraint("split in ('official','development')", name="ck_holdout_manifest_split"),
        sa.CheckConstraint("retention_class in ('official','development','ephemeral')", name="ck_holdout_manifest_retention"),
        sa.UniqueConstraint("task_revision_id", "manifest_digest", name="uq_holdout_manifest_revision_digest"),
    )
    op.create_index("ix_holdout_manifest_task_status", "holdout_manifest", ["task_revision_id", "status"])

    op.create_table(
        "holdout_review",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("holdout_manifest_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("holdout_manifest.id"), nullable=False),
        sa.Column("reviewer_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("review_kind", sa.String(32), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("evidence_digest", sa.String(64), nullable=False),
        sa.Column("report", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("review_kind in ('overlap','storage','isolation','manifest')", name="ck_holdout_review_kind"),
        sa.CheckConstraint("decision in ('approve','reject','inconclusive')", name="ck_holdout_review_decision"),
        sa.UniqueConstraint("holdout_manifest_id", "review_kind", "reviewer_user_id", name="uq_holdout_review_identity"),
    )
    op.create_index("ix_holdout_review_manifest", "holdout_review", ["holdout_manifest_id"])

    op.create_table(
        "holdout_access_audit",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("holdout_manifest_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("holdout_manifest.id"), nullable=False),
        sa.Column("actor_identity", sa.String(256), nullable=False),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("campaign.id"), nullable=True),
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("attempt.id"), nullable=True),
        sa.Column("object_digest", sa.String(64), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("operation in ('read','list','download','verify','review')", name="ck_holdout_access_operation"),
    )
    op.create_index("ix_holdout_access_manifest_time", "holdout_access_audit", ["holdout_manifest_id", "created_at"])
    op.create_index("ix_holdout_access_campaign", "holdout_access_audit", ["campaign_id"])

    op.execute("""
    CREATE OR REPLACE FUNCTION aieb_guard_holdout_manifest() RETURNS trigger AS $$
    DECLARE expected_family text; expected_evaluator_digest text;
    BEGIN
      IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'holdout manifests are append-only';
      END IF;
      SELECT tr.family_id, er.code_digest INTO expected_family, expected_evaluator_digest
        FROM task_revision tr JOIN evaluator_revision er ON er.id = tr.evaluator_id
        WHERE tr.id = NEW.task_revision_id;
      IF expected_family IS NULL OR NEW.family_id IS DISTINCT FROM expected_family
        OR NEW.evaluator_revision_digest IS DISTINCT FROM expected_evaluator_digest THEN
        RAISE EXCEPTION 'holdout task family/evaluator identity does not match task_revision';
      END IF;
      IF NEW.storage_uri !~ '^(s3|gs|private)://[^[:space:]]+$'
        OR NEW.object_digest !~ '^[0-9a-f]{64}$'
        OR (NEW.overlap_review_digest IS NOT NULL AND NEW.overlap_review_digest !~ '^[0-9a-f]{64}$') THEN
        RAISE EXCEPTION 'holdout storage or digest identity is invalid';
      END IF;
      IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'draft' THEN
          RAISE EXCEPTION 'holdout manifests must be created as draft';
        END IF;
        RETURN NEW;
      END IF;
      IF OLD.status = 'retired' AND NEW.status <> 'retired' THEN
        RAISE EXCEPTION 'retired holdout manifests cannot change state';
      END IF;
      IF OLD.status = 'frozen' AND NEW.status NOT IN ('frozen','retired') THEN
        RAISE EXCEPTION 'frozen holdout manifests can only be retired';
      END IF;
      IF OLD.status = 'frozen' AND (NEW.task_revision_id IS DISTINCT FROM OLD.task_revision_id
        OR NEW.family_id IS DISTINCT FROM OLD.family_id
        OR NEW.evaluator_revision_digest IS DISTINCT FROM OLD.evaluator_revision_digest
        OR NEW.storage_uri IS DISTINCT FROM OLD.storage_uri
        OR NEW.object_digest IS DISTINCT FROM OLD.object_digest
        OR NEW.object_length IS DISTINCT FROM OLD.object_length
        OR NEW.manifest_digest IS DISTINCT FROM OLD.manifest_digest
        OR NEW.overlap_review_digest IS DISTINCT FROM OLD.overlap_review_digest
        OR NEW.protocol_version IS DISTINCT FROM OLD.protocol_version
        OR NEW.access_scope IS DISTINCT FROM OLD.access_scope
        OR NEW.split IS DISTINCT FROM OLD.split
        OR NEW.retention_class IS DISTINCT FROM OLD.retention_class
        OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
        OR NEW.supersedes_id IS DISTINCT FROM OLD.supersedes_id
        OR NEW.created_by_user_id IS DISTINCT FROM OLD.created_by_user_id
        OR NEW.frozen_by_user_id IS DISTINCT FROM OLD.frozen_by_user_id
        OR NEW.frozen_at IS DISTINCT FROM OLD.frozen_at) THEN
        RAISE EXCEPTION 'frozen holdout manifest identity is immutable';
      END IF;
      IF NEW.status = 'frozen' AND OLD.status <> 'frozen' THEN
        IF NEW.frozen_by_user_id IS NULL OR NEW.frozen_by_user_id = NEW.created_by_user_id OR NEW.frozen_at IS NULL THEN
          RAISE EXCEPTION 'holdout freezer must be distinct from the manifest author';
        END IF;
        IF (SELECT count(*) FROM (
              SELECT DISTINCT ON (review_kind) review_kind, decision
              FROM holdout_review WHERE holdout_manifest_id = OLD.id
              ORDER BY review_kind, created_at DESC, id DESC
            ) latest WHERE latest.decision = 'approve'
              AND latest.review_kind IN ('overlap','storage','isolation','manifest')) <> 4
          OR (SELECT count(DISTINCT reviewer_user_id) FROM (
              SELECT DISTINCT ON (review_kind) review_kind, decision, reviewer_user_id
              FROM holdout_review WHERE holdout_manifest_id = OLD.id
              ORDER BY review_kind, created_at DESC, id DESC
            ) latest WHERE latest.decision = 'approve'
              AND latest.review_kind IN ('overlap','storage','isolation','manifest')) < 2 THEN
          RAISE EXCEPTION 'holdout freeze requires latest approved overlap, storage, isolation, and manifest reviews from two reviewers';
        END IF;
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    CREATE TRIGGER holdout_manifest_guard BEFORE INSERT OR UPDATE OR DELETE ON holdout_manifest
      FOR EACH ROW EXECUTE FUNCTION aieb_guard_holdout_manifest();
    CREATE OR REPLACE FUNCTION aieb_guard_holdout_review() RETURNS trigger AS $$
    DECLARE owner_id uuid; manifest_status text;
    BEGIN
      IF TG_OP <> 'INSERT' THEN RAISE EXCEPTION 'holdout_review is append-only'; END IF;
      SELECT created_by_user_id, status INTO owner_id, manifest_status
        FROM holdout_manifest WHERE id = NEW.holdout_manifest_id;
      IF owner_id IS NULL THEN RAISE EXCEPTION 'holdout manifest does not exist'; END IF;
      IF manifest_status IN ('frozen','retired') THEN
        RAISE EXCEPTION 'frozen or retired holdout manifests cannot receive reviews';
      END IF;
      IF NEW.reviewer_user_id = owner_id THEN
        RAISE EXCEPTION 'holdout author cannot review its own manifest';
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    CREATE TRIGGER holdout_review_guard BEFORE INSERT OR UPDATE OR DELETE ON holdout_review
      FOR EACH ROW EXECUTE FUNCTION aieb_guard_holdout_review();
    CREATE OR REPLACE FUNCTION aieb_guard_holdout_append_only() RETURNS trigger AS $$
    BEGIN
      IF TG_OP <> 'INSERT' THEN RAISE EXCEPTION '% is append-only', TG_TABLE_NAME; END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    CREATE TRIGGER holdout_access_append_only BEFORE UPDATE OR DELETE ON holdout_access_audit
      FOR EACH ROW EXECUTE FUNCTION aieb_guard_holdout_append_only();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS holdout_access_append_only ON holdout_access_audit")
    op.execute("DROP TRIGGER IF EXISTS holdout_review_guard ON holdout_review")
    op.execute("DROP TRIGGER IF EXISTS holdout_manifest_guard ON holdout_manifest")
    op.execute("DROP FUNCTION IF EXISTS aieb_guard_holdout_append_only()")
    op.execute("DROP FUNCTION IF EXISTS aieb_guard_holdout_review()")
    op.execute("DROP FUNCTION IF EXISTS aieb_guard_holdout_manifest()")
    op.drop_index("ix_holdout_access_campaign", table_name="holdout_access_audit")
    op.drop_index("ix_holdout_access_manifest_time", table_name="holdout_access_audit")
    op.drop_table("holdout_access_audit")
    op.drop_index("ix_holdout_review_manifest", table_name="holdout_review")
    op.drop_table("holdout_review")
    op.drop_index("ix_holdout_manifest_task_status", table_name="holdout_manifest")
    op.drop_table("holdout_manifest")
