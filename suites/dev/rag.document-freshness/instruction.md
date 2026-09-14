# Repair stale document ingestion

Updated documents can return old passages and deleted documents remain searchable. Repair incremental ingestion while preserving search for unaffected documents.

The service accepts sequential document events. A document has an integer version. Lower versions cannot supersede newer state; identical id/version/text events are idempotent; equal versions with different text conflict. Deletion creates a tombstone at its version, so stale writes cannot resurrect content. A higher-version document event may recreate a deleted document.

Updates must be queryable before their mutation response completes. Search hits must expose the current `id`, `version`, `chunk_id`, and `text`. A mutation may rewrite that document's chunks, but it must not rebuild unrelated document entries. Do not modify `dev_tests/`.

The expected repair is behavioral, not a required patch shape. No concurrency behavior is required for this task.
