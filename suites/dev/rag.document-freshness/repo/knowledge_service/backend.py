"""Deliberately defective append-only document backend for RAG-01."""

from __future__ import annotations

import json
import os
import urllib.request


class Backend:
    """Baseline defect: every ingest appends a searchable historical chunk."""

    def __init__(self) -> None:
        self.chunks: list[dict[str, object]] = []

    def _write(self, operation: str, document_id: str) -> None:
        endpoint = os.environ.get("AIEB_LEDGER_URL")
        if endpoint:
            request = urllib.request.Request(endpoint, data=json.dumps({"operation": operation, "document_id": document_id}).encode(), headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(request, timeout=2).read()

    def put(self, document_id: str, version: int, text: str, metadata: dict[str, object] | None) -> tuple[int, dict[str, object]]:
        self.chunks.append({"id": document_id, "version": version, "text": text, "metadata": metadata or {}, "chunk_id": f"{document_id}:{version}:0"})
        self._write("append", document_id)
        return 201, {"status": "stored", "version": version}

    def delete(self, document_id: str, version: int) -> tuple[int, dict[str, object]]:
        self._write("delete", document_id)
        return 200, {"status": "accepted", "version": version}

    def search(self, query: str, top_k: int, metadata: dict[str, object] | None) -> list[dict[str, object]]:
        wanted = query.casefold()
        hits = [chunk for chunk in self.chunks if wanted in str(chunk["text"]).casefold() and _metadata_matches(chunk["metadata"], metadata)]
        return hits[:top_k]


def _metadata_matches(actual: object, expected: dict[str, object] | None) -> bool:
    return not expected or all(isinstance(actual, dict) and actual.get(key) == value for key, value in expected.items())
