"""Alternative repair: immutable entries plus an authoritative current pointer."""

from __future__ import annotations

import json
import os
import urllib.request


def _record(operation: str, document_id: str) -> None:
    endpoint = os.environ.get("AIEB_LEDGER_URL")
    if endpoint:
        request = urllib.request.Request(endpoint, data=json.dumps({"operation": operation, "document_id": document_id}).encode(), headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(request, timeout=2).read()


class Backend:
    def __init__(self) -> None:
        self.entries: dict[str, list[dict[str, object]]] = {}
        self.current: dict[str, dict[str, object]] = {}

    def _accept(self, document_id: str, version: int, text: str | None, metadata: dict[str, object], deleted: bool) -> tuple[int, dict[str, object]]:
        old = self.current.get(document_id)
        if old is not None:
            if version < int(old["version"]) or (version == int(old["version"]) and bool(old["deleted"])):
                return 200, {"status": "ignored", "version": old["version"]}
            if version == int(old["version"]):
                if old["text"] == text and old["metadata"] == metadata and bool(old["deleted"]) == deleted:
                    return 200, {"status": "idempotent", "version": version}
                return 409, {"status": "conflict", "version": version}
        entry = {"version": version, "text": text, "metadata": metadata, "deleted": deleted}
        self.entries.setdefault(document_id, []).append(entry)
        self.current[document_id] = entry
        _record("append-current", document_id)
        return (200 if deleted else 201), {"status": "deleted" if deleted else "stored", "version": version}

    def put(self, document_id: str, version: int, text: str, metadata: dict[str, object] | None) -> tuple[int, dict[str, object]]:
        return self._accept(document_id, version, text, metadata or {}, False)

    def delete(self, document_id: str, version: int) -> tuple[int, dict[str, object]]:
        return self._accept(document_id, version, None, {}, True)

    def search(self, query: str, top_k: int, metadata: dict[str, object] | None) -> list[dict[str, object]]:
        wanted = query.casefold()
        hits = []
        for document_id, item in sorted(self.current.items()):
            if bool(item["deleted"]) or wanted not in str(item["text"]).casefold():
                continue
            if metadata and any(item["metadata"].get(key) != value for key, value in metadata.items()):
                continue
            version = int(item["version"])
            hits.append({"id": document_id, "version": version, "chunk_id": f"{document_id}:{version}:0", "text": item["text"]})
        return hits[:top_k]
