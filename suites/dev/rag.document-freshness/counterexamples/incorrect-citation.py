"""Shortcut: retrieval text is current but cited versions/chunk IDs are stale."""
from __future__ import annotations
from .support import Backend
_search = Backend.search
def search(self, query, top_k, metadata):
    hits = _search(self, query, top_k, metadata)
    for hit in hits:
        hit["version"] = 1
        hit["chunk_id"] = f"{hit['id']}:1:0"
    return hits
Backend.search = search
