# Repair the corpus search index

The corpus knowledge service stores versioned documents in a SQLite store and
serves full-text search from a separate FTS5 index. Both are managed through
the vendored `sqlite_utils` package (real upstream sqlite-utils 3.38 code -
read it freely; keep it unmodified). Search is hitting the wrong data:
updated documents return stale passages, deleted documents stay searchable,
and older writes can silently overwrite newer state.

The service accepts sequential document events. A document has an integer
version. Lower versions cannot supersede newer state; identical id/version/
text events are idempotent; equal versions with different text conflict.
Deletion creates a tombstone at its version, so stale writes cannot resurrect
content. A higher-version document event may recreate a deleted document.

Updates must be queryable before their mutation response completes. Search
hits must expose the current `id`, `version`, `chunk_id` (shape
`{id}:{version}:0`), and `text`. A mutation may rewrite that document's index
entries, but it must not rebuild or delete index entries belonging to
unrelated documents (the trusted evaluator observes write scope over an
external write ledger). Search honors the exact-match metadata filter the
caller supplies.

The expected repair is behavioral, not a required patch shape. A full global
rebuild of the index on every mutation is not acceptable. Do not modify
`dev_tests/`. Run `python dev_tests/selfcheck.py` from the repository root to
check the published contract; the trusted evaluator additionally uses a
held-out real-text corpus.
