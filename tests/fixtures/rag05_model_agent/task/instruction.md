# Repair the corpus search index

The repository checked out at `/workspace` is a real, working knowledge
service built on the vendored `sqlite_utils` package (real upstream
sqlite-utils 3.38 code - read it freely; keep it unmodified). Search is
hitting the wrong data: updated documents return stale passages, deleted
documents stay searchable, and older writes can silently overwrite newer
state.

The service accepts sequential document events. A document has an integer
version. Lower versions cannot supersede newer state; identical id/version/
text events are idempotent; equal versions with different text conflict.
Deletion creates a tombstone at its version, so stale writes cannot resurrect
content. A higher-version document event may recreate a deleted document.

Updates must be queryable before their mutation response completes. Search
hits must expose the current `id`, `version`, `chunk_id` (shape
`{id}:{version}:0`), and `text`. A mutation may rewrite that document's index
entries, but it must not rebuild or delete index entries belonging to
unrelated documents. Search honors the exact-match metadata filter the caller
supplies.

The expected repair is behavioral, not a required patch shape. A full global
rebuild of the index on every mutation is not acceptable. Do not modify
`dev_tests/`.

Workflow: read `contracts/application-api.md` for the HTTP contract, run
`python dev_tests/selfcheck.py` from `/workspace` to check the published
contract over toy data (the final evaluation uses a held-out real-text corpus
against the same contract), then fix `knowledge_service/backend.py` (sibling
modules inside `knowledge_service/` are allowed). When done, call `submit`.
