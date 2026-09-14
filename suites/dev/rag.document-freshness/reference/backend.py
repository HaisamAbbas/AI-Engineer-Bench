"""Reference repair: replace current chunks and retain durable tombstones."""

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
        self.current: dict[str, dict[str, object]] = {}

    def put(self, document_id: str, version: int, text: str, metadata: dict[str, object] | None) -> tuple[int, dict[str, object]]:
        old = self.current.get(document_id)
        if old is not None:
            old_version = int(old["version"])
            if version < old_version or (version == old_version and bool(old["deleted"])):
                return 200, {"status": "ignored", "version": old_version}
            if version == old_version:
                if old["text"] == text and old["metadata"] == (metadata or {}):
                    return 200, {"status": "idempotent", "version": version}
                return 409, {"status": "conflict", "version": version}
        self.current[document_id] = {"version": version, "text": text, "metadata": metadata or {}, "deleted": False}
        _record("replace", document_id)
        return 201, {"status": "stored", "version": version}

    def delete(self, document_id: str, version: int) -> tuple[int, dict[str, object]]:
        old = self.current.get(document_id)
        if old is not None and version < int(old["version"]):
            return 200, {"status": "ignored", "version": old["version"]}
        if old is not None and version == int(old["version"]) and bool(old["deleted"]):
            return 200, {"status": "idempotent", "version": version}
        self.current[document_id] = {"version": version, "text": None, "metadata": {}, "deleted": True}
        _record("tombstone", document_id)
        return 200, {"status": "deleted", "version": version}

    def search(self, query: str, top_k: int, metadata: dict[str, object] | None) -> list[dict[str, object]]:
        wanted = query.casefold()
        hits = []
        for document_id, item in sorted(self.current.items()):
            if bool(item["deleted"]) or wanted not in str(item["text"]).casefold():
                continue
            actual = item["metadata"]
            if metadata and (not isinstance(actual, dict) or any(actual.get(key) != value for key, value in metadata.items())):
                continue
            version = int(item["version"])
            hits.append({"id": document_id, "version": version, "chunk_id": f"{document_id}:{version}:0", "text": item["text"]})
        return hits[:top_k]
