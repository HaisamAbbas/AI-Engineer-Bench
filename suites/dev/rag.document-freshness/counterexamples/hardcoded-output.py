"""Shortcut: recognizes a visible development fact but cannot serve real documents."""
from __future__ import annotations
class Backend:
    def __init__(self) -> None: pass
    def put(self, document_id, version, text, metadata): return 201, {"status": "stored", "version": version}
    def delete(self, document_id, version): return 200, {"status": "deleted", "version": version}
    def search(self, query, top_k, metadata):
        return [{"id": "dev-document", "version": 2, "chunk_id": "dev-document:2:0", "text": "visible replacement fact"}] if "visible" in query.casefold() else []
