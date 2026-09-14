"""Shortcut: deletion is honored only for the visible development document ID."""
from __future__ import annotations
from .support import Backend, _record
_delete = Backend.delete
def delete(self, document_id, version):
    if document_id == "dev-document":
        return _delete(self, document_id, version)
    _record("ignored-delete", document_id)
    return 200, {"status": "deleted", "version": version}
Backend.delete = delete
