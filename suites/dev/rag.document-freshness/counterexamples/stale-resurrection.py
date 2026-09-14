"""Shortcut: drops tombstone metadata, allowing stale post-delete reinsertions."""
from __future__ import annotations
from .support import Backend, _record
def delete(self, document_id, version):
    self.current.pop(document_id, None)
    _record("delete-without-tombstone", document_id)
    return 200, {"status": "deleted", "version": version}
Backend.delete = delete
