"""Corpus knowledge service backend for RAG-05.

Document state lives in a ``documents`` table (the store) while full-text
search is served from a separate standalone FTS5 table ``corpus_fts`` (the
search index). Both are managed through the vendored ``sqlite_utils`` package
(real upstream sqlite-utils 3.38 code - see NOTICE.md).

This baseline is deliberately defective in two realistic ways:

* Version semantics are missing: every write overwrites current state
  regardless of version ordering, and deletes leave no tombstone behind, so
  stale writes can resurrect superseded or deleted content.
* The search index is append-only: ``corpus_fts`` rows are inserted on every
  accepted write and never removed, so superseded chunks stay searchable and
  deleted documents keep surfacing stale hits (classic index/store drift).
"""

from __future__ import annotations

import json
import os
import sqlite3
import urllib.request

import sqlite_utils

FTS_SCHEMA = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS corpus_fts USING fts5 ("
    "document_id UNINDEXED, version UNINDEXED, metadata UNINDEXED, "
    "chunk_id UNINDEXED, text)"
)


class Backend:
    def __init__(self) -> None:
        # ThreadingHTTPServer serves requests on worker threads; the sqlite
        # connection must allow cross-thread use (the evaluator is sequential).
        self._db = sqlite_utils.Database(sqlite3.connect(":memory:", check_same_thread=False))
        self._documents = self._db["documents"]
        self._db.execute(FTS_SCHEMA)
        self._index = self._db["corpus_fts"]

    def _record(self, operation: str, document_id: str) -> None:
        endpoint = os.environ.get("AIEB_LEDGER_URL")
        if endpoint:
            request = urllib.request.Request(
                endpoint,
                data=json.dumps({"operation": operation, "document_id": document_id}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(request, timeout=2).read()

    def put(
        self, document_id: str, version: int, text: str, metadata: dict[str, object] | None
    ) -> tuple[int, dict[str, object]]:
        payload = json.dumps(metadata or {}, sort_keys=True)
        # Defect: no version guard - any write overwrites current state.
        self._documents.insert(
            {"id": document_id, "version": version, "text": text, "metadata": payload},
            pk="id",
            replace=True,
        )
        # Defect: the search index only ever grows.
        self._index.insert(
            {
                "document_id": document_id,
                "version": version,
                "metadata": payload,
                "chunk_id": f"{document_id}:{version}:0",
                "text": text,
            }
        )
        self._record("upsert", document_id)
        return 201, {"status": "stored", "version": version}

    def delete(self, document_id: str, version: int) -> tuple[int, dict[str, object]]:
        # Defect: the current row is dropped but no tombstone survives, so a
        # stale write can resurrect the content.
        self._documents.delete(document_id)
        self._record("delete", document_id)
        return 200, {"status": "deleted", "version": version}

    def search(
        self, query: str, top_k: int, metadata: dict[str, object] | None
    ) -> list[dict[str, object]]:
        hits: list[dict[str, object]] = []
        for row in self._index.search(query):
            row_metadata = json.loads(str(row["metadata"]))
            if metadata and any(row_metadata.get(key) != value for key, value in metadata.items()):
                continue
            hits.append(
                {
                    "id": str(row["document_id"]),
                    "version": int(row["version"]),
                    "chunk_id": str(row["chunk_id"]),
                    "text": str(row["text"]),
                }
            )
        return hits[:top_k]

