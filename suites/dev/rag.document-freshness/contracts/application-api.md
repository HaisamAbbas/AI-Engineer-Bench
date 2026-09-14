# RAG-01 public application contract

| Endpoint | Input | Required behavior |
| --- | --- | --- |
| `GET /health` | none | returns `{"status":"ready"}` |
| `POST /documents` | `id`, integer `version`, `text`, optional `metadata` | stores the newest accepted version; same id/version/text is idempotent; equal version with different payload is conflict |
| `DELETE /documents/{id}` | JSON body with integer `version` | records a versioned tombstone; stale writes cannot resurrect it |
| `POST /search` | `query`, `top_k`, optional `metadata` | returns hits with `id`, `version`, `chunk_id`, `text`; only current nondeleted versions are searchable |

Public requirements:

- `latest-version-visible`: a successful higher version replaces stale search content before its response returns.
- `idempotent-event-replay`: repeating an accepted identical event does not duplicate search hits.
- `lower-version-rejected`: lower-version writes never supersede newer document/tombstone state.
- `deleted-content-absent`: tombstoned content is not searchable and stale reinsertions do not resurrect it.
- `higher-version-recreation`: a valid higher version after deletion is searchable at that higher version.
- `unaffected-documents-preserved`: mutations retain current unrelated records.
- `incremental-unaffected-write-scope`: a mutation may write its own document but must not rebuild/delete unrelated backend records.
- `citation-version-mapping`: every hit cites the current document version and current chunk ID.
