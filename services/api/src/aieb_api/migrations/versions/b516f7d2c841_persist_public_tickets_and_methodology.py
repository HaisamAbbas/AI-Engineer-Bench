"""Persist public task tickets and frozen protocol revisions (ENG-016).

Task prose is stored outside task manifests so presentation edits do not alter
benchmark identity. Known development task tickets are backfilled from this
immutable migration payload. Protocol versions already used by frozen
campaigns are copied into a queryable revision table; conflicting payloads
for one version abort migration rather than choosing one arbitrarily.

Revision ID: b516f7d2c841
Revises: f2b6c9a417de
Create Date: 2026-09-16 15:00:00.000000
"""
from __future__ import annotations

from uuid import uuid4

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b516f7d2c841"
down_revision = "f2b6c9a417de"
branch_labels = None
depends_on = None

TASK_TICKETS = '{"ext.batch-alignment": "# EXT-02: Preserve document/output correspondence\\n\\nRepair the batch extraction service. Output order is not stable and documents may fail independently. The public API must return each successful result with its correct document ID, retain all valid documents, and apply the declared repeated-ID rule: the last occurrence in one request wins.", "ext.missingness": "# EXT-01: Preserve missing values\\n\\nRepair the extraction response so absent source fields remain absent. Preserve supplied zero,\\nempty-string, and null values exactly; do not infer a replacement.", "ext.partial-batch": "# EXT-04: Retain valid partial-batch outputs\\n\\nRepair batch extraction so one malformed item cannot discard valid results before or after it.\\nReturn per-item failures and preserve all successful outputs.", "ext.unit-normalization": "# EXT-03: Normalize units exactly\\n\\nRepair declared mass-unit normalization. Use the published conversions and tolerance while\\nretaining original evidence and unaffected fields.", "rag.citation-current-span": "# RAG-03: Preserve current citation spans\\n\\nRepair citations after a document update. Search results must cite the current source span and chunk ID, not a stale offset from an older chunk.", "rag.document-freshness": "# Repair stale document ingestion\\n\\nUpdated documents can return old passages and deleted documents remain searchable. Repair incremental ingestion while preserving search for unaffected documents.\\n\\nThe service accepts sequential document events. A document has an integer version. Lower versions cannot supersede newer state; identical id/version/text events are idempotent; equal versions with different text conflict. Deletion creates a tombstone at its version, so stale writes cannot resurrect content. A higher-version document event may recreate a deleted document.\\n\\nUpdates must be queryable before their mutation response completes. Search hits must expose the current `id`, `version`, `chunk_id`, and `text`. A mutation may rewrite that document\'s chunks, but it must not rebuild unrelated document entries. Do not modify `dev_tests/`.\\n\\nThe expected repair is behavioral, not a required patch shape. No concurrency behavior is required for this task.", "rag.embedding-version": "# RAG-04: Keep embedding spaces compatible\\n\\nRepair the index so queries never silently compare vectors across embedding versions. Migrate explicitly or reject incompatible queries safely.", "rag.metadata-filter-topk": "# RAG-02: Apply metadata filters before ranking\\n\\nRepair the search service. Metadata filters must restrict the candidate set before `top_k` is applied; relevant matching records below the unfiltered cutoff must remain discoverable.", "tool.corrected-arguments": "# TOOL-04: Use corrected arguments\\n\\nRepair the pending-action logic so a user correction replaces stale tool arguments before execution.", "tool.false-completion": "# TOOL-01: Report operation outcomes honestly\\n\\nRepair the workflow service. It invokes the documented external operation service. A failed operation must not be reported complete; genuine successful operations must still complete. Do not infer outcome from a candidate log.", "tool.idempotent-write": "# TOOL-02: Deduplicate ambiguous write retries\\n\\nAn operation can commit then lose its response. Repair the retry path so it resolves the\\nrequested operation without duplicating its external effect.", "tool.session-isolation": "# TOOL-03: Isolate session state\\n\\nRepair state handling so concurrent synthetic user sessions do not leak state across users."}'


def upgrade() -> None:
    op.add_column("task_revision", sa.Column("ticket_text", sa.Text(), nullable=True))
    bind = op.get_bind()
    import json
    # Frozen task rows are protected by an UPDATE trigger. This one-time
    # additive backfill fills the newly introduced presentation-only column;
    # temporarily remove and restore that trigger inside the same migration
    # transaction so normal revision fields remain immutable afterward.
    op.execute("DROP TRIGGER task_revision_immutable ON task_revision")
    for slug, ticket in json.loads(TASK_TICKETS).items():
        bind.execute(
            sa.text("UPDATE task_revision SET ticket_text = :ticket WHERE slug = :slug AND ticket_text IS NULL"),
            {"slug": slug, "ticket": ticket},
        )
    op.execute("""
        CREATE TRIGGER task_revision_immutable
        BEFORE UPDATE ON task_revision
        FOR EACH ROW EXECUTE FUNCTION aieb_reject_revision_update();
    """)

    op.create_table(
        "protocol_revision",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.String(length=128), nullable=False),
        sa.Column("scoring_digest", sa.String(length=64), nullable=False),
        sa.Column("manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("version", name="uq_protocol_revision_version"),
    )
    op.execute("""
        CREATE TRIGGER protocol_revision_immutable
        BEFORE UPDATE ON protocol_revision
        FOR EACH ROW EXECUTE FUNCTION aieb_reject_revision_update();
    """)
    protocols = bind.execute(sa.text("""
        SELECT DISTINCT resolved->'protocol' AS manifest
        FROM campaign
        WHERE jsonb_typeof(resolved->'protocol') = 'object'
    """)).scalars().all()
    by_version = {}
    for manifest in protocols:
        version = manifest.get("id")
        digest = manifest.get("scoring_digest")
        if not isinstance(version, str) or not isinstance(digest, str):
            raise RuntimeError("frozen campaign contains a malformed protocol revision")
        prior = by_version.get(version)
        if prior is not None and prior != manifest:
            raise RuntimeError(f"frozen campaigns disagree about protocol version {version!r}")
        by_version[version] = manifest
    for version, manifest in by_version.items():
        bind.execute(
            sa.text("""
                INSERT INTO protocol_revision (id, version, scoring_digest, manifest)
                VALUES (:id, :version, :digest, CAST(:manifest AS jsonb))
            """),
            {"id": str(uuid4()), "version": version, "digest": manifest["scoring_digest"], "manifest": __import__("json").dumps(manifest)},
        )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS protocol_revision_immutable ON protocol_revision")
    op.drop_table("protocol_revision")
    op.drop_column("task_revision", "ticket_text")
