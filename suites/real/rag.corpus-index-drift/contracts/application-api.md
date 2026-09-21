# RAG-05 public application contract

| Endpoint | Input | Required behavior |
| --- | --- | --- |
| `GET /health` | none | returns `{"status":"ready"}` |
| `POST /documents` | `id`, integer `version`, `text`, optional `metadata` | stores the newest accepted version; same id/version/text is idempotent; equal version with different payload is conflict |
| `DELETE /documents/{id}` | JSON body with integer `version` | records a versioned tombstone; stale writes cannot resurrect it |
| `POST /search` | `query`, `top_k`, optional `metadata` | returns hits with `id`, `version`, `chunk_id`, `text`; only current nondeleted versions are searchable; hits echo the stored `metadata` and the filter matches it exactly |

Public requirements:

- `latest-version-visible`: a successful higher version replaces stale search content before its response returns.
- `stale-version-absent`: text from superseded versions is no longer searchable.
- `idempotent-event-replay`: repeating an accepted identical event does not duplicate search hits.
- `lower-version-rejected`: lower-version writes never supersede newer document/tombstone state.
- `equal-version-conflict`: equal-version writes with a different payload return conflict.
- `deleted-content-absent`: tombstoned content is not searchable and stale reinsertions do not resurrect it.
- `higher-version-recreation`: a valid higher version after deletion is searchable at that higher version.
- `metadata-filter-respected`: a supplied metadata filter restricts hits to documents whose stored metadata matches it exactly.
- `full-text-match`: multi-token queries match documents containing all tokens of the query.
- `unaffected-documents-preserved`: mutations retain current unrelated records.
- `incremental-write-scope`: a mutation may write its own document's index entries but must not rebuild/delete entries of unrelated documents.
- `citation-version-mapping`: every hit cites the current document version and current chunk ID (`{id}:{version}:0`).
