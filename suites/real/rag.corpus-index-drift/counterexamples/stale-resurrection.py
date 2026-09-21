"""Counterexample: correct repair semantics except delete drops the row
without recording a tombstone, so a stale write can resurrect deleted
content. Fails deleted-content-absent only."""

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

    def _current(self, document_id: str) -> dict[str, object] | None:
        rows = list(self._documents.rows_where("id = ?", [document_id]))
        return rows[0] if rows else None

    def _reindex(self, document_id: str, version: int, text: str, payload: str) -> None:
        self._db.execute("DELETE FROM corpus_fts WHERE document_id = ?", [document_id])
        self._index.insert(
            {
                "document_id": document_id,
                "version": version,
                "metadata": payload,
                "chunk_id": f"{document_id}:{version}:0",
                "text": text,
            }
        )

    def put(
        self, document_id: str, version: int, text: str, metadata: dict[str, object] | None
    ) -> tuple[int, dict[str, object]]:
        payload = json.dumps(metadata or {}, sort_keys=True)
        current = self._current(document_id)
        if current is not None:
            current_version = int(current["version"])
            if current["text"] is None or int(current["deleted"]):
                if version <= current_version:
                    return 200, {"status": "ignored", "version": current_version}
            elif version < current_version:
                return 200, {"status": "ignored", "version": current_version}
            elif version == current_version:
                if current["text"] == text and current["metadata"] == payload:
                    return 200, {"status": "idempotent", "version": version}
                return 409, {"status": "conflict", "version": version}
        self._documents.insert(
            {"id": document_id, "version": version, "text": text, "metadata": payload, "deleted": 0},
            pk="id",
            replace=True,
        )
        self._reindex(document_id, version, text, payload)
        self._record("upsert", document_id)
        return 201, {"status": "stored", "version": version}

    def delete(self, document_id: str, version: int) -> tuple[int, dict[str, object]]:
        current = self._current(document_id)
        if current is None:
            return 200, {"status": "idempotent", "version": version}
        current_version = int(current["version"])
        if current["text"] is not None and not int(current["deleted"]) and version < current_version:
            return 200, {"status": "ignored", "version": current_version}
        # Defect: no tombstone survives the delete.
        self._documents.delete(document_id)
        self._db.execute("DELETE FROM corpus_fts WHERE document_id = ?", [document_id])
        self._record("tombstone", document_id)
        return 200, {"status": "deleted", "version": version}

    def search(
        self, query: str, top_k: int, metadata: dict[str, object] | None
    ) -> list[dict[str, object]]:
        hits: list[dict[str, object]] = []
        for row in self._index.search(query):
            document = self._current(str(row["document_id"]))
            if document is None or int(document["deleted"]):
                continue
            if int(document["version"]) != int(row["version"]):
                continue
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
