"""harden worker artifact store: retention/staging metadata, candidate linkage, and real size enforcement

ENG-015 review (fifth pass, then a further independent review found this
work's own gaps): kept as a SEPARATE migration rather than editing
e20d5d09b489 in place (review finding #3) - that revision was already
committed in 47b90e6 before this hardening began, so any database that had
already applied it would never receive columns added by rewriting that
file, while the ORM (which only sees this file's current, edited state)
would expect them regardless. Adds, via ALTER TABLE, on top of the
unmodified original schema:

- worker_artifact_blob.retention_class ('staging' | 'evidence') and
  staged_until, implementing spec section 37's staging/orphan semantics
  (unreferenced staging objects expire after 24 hours by default; committed
  evidence is never deleted by that cleanup job);
- a REAL size guarantee at the database level (review finding #5): a prior
  version of this hardening only checked byte_length (a caller-supplied
  column) against the submission policy's cap, so a direct INSERT could
  claim byte_length=1 while storing up to 100 MiB of actual `data` bytes -
  reproduced directly. octet_length(data) must now equal byte_length AND
  stay within the cap, and byte_length itself must be nonnegative;
- worker_artifact_reference.candidate_id, tying each reference to the
  candidate whose engineering phase created it (nullable - stays NULL for
  an unclaimed staging reference, and for any reference that predates this
  column), with lookup indexes on candidate_id and blob_sha256.

Revision ID: f2b6c9a417de
Revises: e20d5d09b489
Create Date: 2026-09-16 13:00:00.000000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = 'f2b6c9a417de'
down_revision = 'e20d5d09b489'
branch_labels = None
depends_on = None

MAX_WORKER_ARTIFACT_BYTES = 52_428_800


def upgrade() -> None:
    op.add_column(
        'worker_artifact_blob',
        sa.Column('retention_class', sa.String(length=16), nullable=False, server_default='staging'),
    )
    op.add_column(
        'worker_artifact_blob',
        sa.Column('staged_until', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        'ck_worker_artifact_blob_retention_class', 'worker_artifact_blob', "retention_class in ('staging','evidence')",
    )
    # octet_length(data) = byte_length (review finding #5): byte_length
    # alone is caller-supplied and was never checked against the actual
    # stored bytes - a row could previously claim any byte_length regardless
    # of len(data). Nonnegative is enforced as part of the same constraint
    # rather than a separate one, since a negative byte_length can never
    # equal a real octet_length() result anyway.
    op.create_check_constraint(
        'ck_worker_artifact_blob_byte_length_matches_data',
        'worker_artifact_blob',
        'byte_length >= 0 AND octet_length(data) = byte_length',
    )
    op.create_check_constraint(
        'ck_worker_artifact_blob_max_bytes',
        'worker_artifact_blob',
        f'octet_length(data) <= {MAX_WORKER_ARTIFACT_BYTES}',
    )
    op.add_column(
        'worker_artifact_reference',
        sa.Column('candidate_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('candidate.id'), nullable=True),
    )
    op.create_index('ix_worker_artifact_reference_candidate', 'worker_artifact_reference', ['candidate_id'])
    op.create_index('ix_worker_artifact_reference_blob', 'worker_artifact_reference', ['blob_sha256'])

    # Upgrade data written after e20d5d09b489 was applied but before these
    # ownership columns existed. Claim only unambiguous references whose
    # stored scope, visibility, digest, and byte length all agree with both
    # the attempt and the database rows. Corrupt or ambiguous legacy records
    # remain unclaimed staging data and retain their normal expiry.
    op.execute(sa.text("""
        WITH raw_candidate_references AS (
            SELECT c.id AS candidate_id,
                   c.attempt_id,
                   entry->'reference' AS reference_data
            FROM candidate AS c
            CROSS JOIN LATERAL jsonb_array_elements(
                CASE
                    WHEN jsonb_typeof(c.stored_candidate->'file_references') = 'array'
                    THEN c.stored_candidate->'file_references'
                    ELSE '[]'::jsonb
                END
            ) AS file_reference(entry)
        ), candidate_references AS (
            SELECT candidate_id,
                   attempt_id,
                   CASE
                       WHEN reference_data->>'id' ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                       THEN (reference_data->>'id')::uuid
                   END AS reference_id,
                   reference_data->>'access_scope' AS access_scope,
                   reference_data->>'visibility' AS visibility,
                   CASE
                       WHEN reference_data->'blob'->>'sha256' ~ '^[0-9a-f]{64}$'
                       THEN reference_data->'blob'->>'sha256'
                   END AS blob_sha256,
                   CASE
                       WHEN jsonb_typeof(reference_data->'blob'->'byte_length') = 'number'
                           AND reference_data->'blob'->>'byte_length' ~ '^(0|[1-9][0-9]{0,9})$'
                       THEN (reference_data->'blob'->>'byte_length')::numeric
                   END AS byte_length
            FROM raw_candidate_references
        ), unambiguous_candidate_references AS (
            SELECT candidate_references.*,
                   count(*) OVER (PARTITION BY reference_id) AS claim_count
            FROM candidate_references
        )
        UPDATE worker_artifact_reference AS r
        SET candidate_id = claims.candidate_id
        FROM unambiguous_candidate_references AS claims
        JOIN worker_artifact_blob AS b
          ON b.sha256 = claims.blob_sha256
         AND b.byte_length = claims.byte_length
        WHERE r.id = claims.reference_id
          AND r.blob_sha256 = claims.blob_sha256
          AND r.access_scope = claims.access_scope
          AND r.access_scope = claims.attempt_id::text
          AND claims.access_scope = claims.attempt_id::text
          AND r.visibility = claims.visibility
          AND claims.claim_count = 1
          AND r.candidate_id IS NULL
    """))
    op.execute(sa.text("""
        UPDATE worker_artifact_blob AS b
        SET retention_class = 'evidence', staged_until = NULL
        WHERE EXISTS (
            SELECT 1 FROM worker_artifact_reference AS r
            WHERE r.blob_sha256 = b.sha256 AND r.candidate_id IS NOT NULL
        )
    """))
    # Data that has no recoverable candidate owner remains staging and can be
    # reclaimed using the same 24-hour lifetime as newly staged artifacts.
    # Use created_at so old, genuinely unclaimed objects can expire promptly.
    op.execute(sa.text("""
        UPDATE worker_artifact_blob
        SET staged_until = created_at + INTERVAL '24 hours'
        WHERE retention_class = 'staging' AND staged_until IS NULL
    """))


def downgrade() -> None:
    op.drop_index('ix_worker_artifact_reference_blob', table_name='worker_artifact_reference')
    op.drop_index('ix_worker_artifact_reference_candidate', table_name='worker_artifact_reference')
    op.drop_column('worker_artifact_reference', 'candidate_id')
    op.drop_constraint('ck_worker_artifact_blob_max_bytes', 'worker_artifact_blob', type_='check')
    op.drop_constraint('ck_worker_artifact_blob_byte_length_matches_data', 'worker_artifact_blob', type_='check')
    op.drop_constraint('ck_worker_artifact_blob_retention_class', 'worker_artifact_blob', type_='check')
    op.drop_column('worker_artifact_blob', 'staged_until')
    op.drop_column('worker_artifact_blob', 'retention_class')
