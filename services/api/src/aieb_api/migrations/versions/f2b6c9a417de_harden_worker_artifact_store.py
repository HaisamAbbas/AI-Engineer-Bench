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


def downgrade() -> None:
    op.drop_index('ix_worker_artifact_reference_blob', table_name='worker_artifact_reference')
    op.drop_index('ix_worker_artifact_reference_candidate', table_name='worker_artifact_reference')
    op.drop_column('worker_artifact_reference', 'candidate_id')
    op.drop_constraint('ck_worker_artifact_blob_max_bytes', 'worker_artifact_blob', type_='check')
    op.drop_constraint('ck_worker_artifact_blob_byte_length_matches_data', 'worker_artifact_blob', type_='check')
    op.drop_constraint('ck_worker_artifact_blob_retention_class', 'worker_artifact_blob', type_='check')
    op.drop_column('worker_artifact_blob', 'staged_until')
    op.drop_column('worker_artifact_blob', 'retention_class')
