"""Alternative repair: same semantics through a different real-API shape.

The store keeps the same version/tombstone behavior, but the searchable index
is managed by sqlite-utils' own external-content FTS5 triggers
(``enable_fts(..., create_triggers=True)``) instead of a manually maintained
projection table. This proves the repair is behavioral, not a required patch
shape.
"""

from __future__ import annotations

import json
import os
import sqlite3
import urllib.request

import sqlite_utils


class Backend:
    def __init__(self) -> None:
        # ThreadingHTTPServer serves requests on worker threads; the sqlite
        # connection must allow cross-thread use (the evaluator is sequential).
        self._db = sqlite_utils.Database(sqlite3.connect(":memory:", check_same_thread=False))
        self._documents = self._db["documents"]
        self._documents.insert(
            {"id": "__schema__", "version": 0, "text": "", "metadata": "{}", "deleted": 0},
            pk="id",
        )
        self._tombstones = self._db["tombstones"]
        # Real sqlite-utils external-content FTS5, kept synchronized by
        # triggers on INSERT/UPDATE/DELETE of the documents table.
        self._documents.enable_fts(["text"], fts_version="FTS5", create_triggers=True, replace=True)

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

    def _tombstone_version(self, document_id: str) -> int | None:
        rows = list(self._tombstones.rows_where("document_id = ?", [document_id]))
        return int(rows[0]["version"]) if rows else None

    def put(
        self, document_id: str, version: int, text: str, metadata: dict[str, object] | None
    ) -> tuple[int, dict[str, object]]:
        payload = json.dumps(metadata or {}, sort_keys=True)
        tombstone_version = self._tombstone_version(document_id)
        current = self._current(document_id)
        if current is not None:
            current_version = int(current["version"])
            if version < current_version or (version == current_version and int(current["deleted"])):
                return 200, {"status": "ignored", "version": current_version}
            if version == current_version:
                if current["text"] == text and current["metadata"] == payload:
                    return 200, {"status": "idempotent", "version": version}
                return 409, {"status": "conflict", "version": version}
        if tombstone_version is not None and version <= tombstone_version:
            return 200, {"status": "ignored", "version": tombstone_version}
        self._documents.insert(
            {
                "id": document_id,
                "version": version,
                "text": text,
                "metadata": payload,
                "deleted": 0,
            },
            pk="id",
            replace=True,
        )
        if tombstone_version is not None:
            self._tombstones.delete(document_id)
        self._record("upsert", document_id)
        return 201, {"status": "stored", "version": version}

    def delete(self, document_id: str, version: int) -> tuple[int, dict[str, object]]:
        current = self._current(document_id)
        if current is None:
            tombstone_version = self._tombstone_version(document_id)
            return 200, {"status": "idempotent", "version": tombstone_version or version}
        current_version = int(current["version"])
        if version < current_version or (
            version == current_version and int(current["deleted"])
        ):
            return 200, {"status": "ignored", "version": current_version}
        # Versioned tombstone row; stale writes cannot resurrect it.
        self._documents.delete(document_id)
        self._tombstones.insert(
            {"document_id": document_id, "version": version}, pk="document_id", replace=True
        )
        self._record("tombstone", document_id)
        return 200, {"status": "deleted", "version": version}

    def search(
        self, query: str, top_k: int, metadata: dict[str, object] | None
    ) -> list[dict[str, object]]:
        hits: list[dict[str, object]] = []
        for row in self._documents.search(query):
            if int(row["deleted"]):
                continue
            row_metadata = json.loads(str(row["metadata"]))
            if metadata and any(row_metadata.get(key) != value for key, value in metadata.items()):
                continue
            hits.append(
                {
                    "id": str(row["id"]),
                    "version": int(row["version"]),
                    "chunk_id": f"{row['id']}:{int(row['version'])}:0",
                    "text": str(row["text"]),
                }
            )
        return hits[:top_k]
