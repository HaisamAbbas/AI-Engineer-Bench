"""V2-GAP-006 structured independent admission/release review evidence.

Task admission already has a dedicated review table.  Release/campaign
reviews historically used the generic ``review.evidence`` JSON object, which
made it possible to omit or mislabel required provenance.  This migration
adds a first-class append-only record for the two release gates and checks the
reviewer's subject identity at the database boundary as well as in the API.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "e7f8a9b0c1d2"
down_revision = "d6e7f8a9b0c1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The task-admission table already carries these fields, but its original
    # migration predated the digest/text checks. Add them here so direct SQL
    # writes cannot create an apparently independent review with malformed
    # provenance either.
    op.create_check_constraint(
        "ck_task_admission_review_digest_format", "task_admission_review",
        "evidence_digest ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        "ck_task_admission_review_scope_nonblank", "task_admission_review",
        "length(btrim(scope)) > 0",
    )
    op.create_check_constraint(
        "ck_task_admission_review_reason_nonblank", "task_admission_review",
        "length(btrim(reason)) > 0",
    )
    op.create_table(
        "independent_review",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reviewer_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("subject_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("scope", sa.String(length=128), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("evidence_digest", sa.String(length=64), nullable=False),
        sa.Column("independence_declaration", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("target_type", "target_id", name="uq_independent_review_target"),
        sa.CheckConstraint(
            "target_type in ('campaign_approval','publication_preparation')",
            name="ck_independent_review_target_type",
        ),
        sa.CheckConstraint("decision in ('approve','reject')", name="ck_independent_review_decision"),
        sa.CheckConstraint("independence_declaration = true", name="ck_independent_review_independence"),
        sa.CheckConstraint("reviewer_user_id <> subject_user_id", name="ck_independent_review_reviewer_distinct"),
        sa.CheckConstraint("evidence_digest ~ '^[0-9a-f]{64}$'", name="ck_independent_review_digest"),
        sa.CheckConstraint("length(btrim(scope)) > 0", name="ck_independent_review_scope"),
        sa.CheckConstraint("length(btrim(reason)) > 0", name="ck_independent_review_reason"),
    )
    op.create_index(
        "ix_independent_review_target", "independent_review", ["target_type", "target_id"]
    )

    # A polymorphic target cannot be represented by a normal FK.  Keep direct
    # SQL writes from naming a convenient but unrelated subject identity.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION aieb_guard_independent_release_review() RETURNS trigger AS $$
        DECLARE expected_subject uuid;
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'independent release reviews are append-only'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            IF NEW.target_type = 'campaign_approval' THEN
                SELECT created_by_user_id INTO expected_subject FROM campaign WHERE id = NEW.target_id;
                IF expected_subject IS NULL OR expected_subject <> NEW.subject_user_id THEN
                    RAISE EXCEPTION 'campaign review subject does not match campaign creator'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
            ELSIF NEW.target_type = 'publication_preparation' THEN
                SELECT prepared_by_user_id INTO expected_subject
                  FROM publication_preparation WHERE id = NEW.target_id;
                IF expected_subject IS NULL OR expected_subject <> NEW.subject_user_id THEN
                    RAISE EXCEPTION 'publication review subject does not match preparation author'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
            ELSE
                RAISE EXCEPTION 'unsupported independent review target %', NEW.target_type
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER independent_release_review_guard
        BEFORE INSERT OR UPDATE OR DELETE ON independent_review
        FOR EACH ROW EXECUTE FUNCTION aieb_guard_independent_release_review();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS independent_release_review_guard ON independent_review")
    op.execute("DROP FUNCTION IF EXISTS aieb_guard_independent_release_review()")
    op.drop_index("ix_independent_review_target", table_name="independent_review")
    op.drop_table("independent_review")
    op.drop_constraint("ck_task_admission_review_reason_nonblank", "task_admission_review", type_="check")
    op.drop_constraint("ck_task_admission_review_scope_nonblank", "task_admission_review", type_="check")
    op.drop_constraint("ck_task_admission_review_digest_format", "task_admission_review", type_="check")
