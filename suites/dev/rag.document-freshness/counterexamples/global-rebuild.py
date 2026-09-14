"""Shortcut: correct-looking state but rebuilds the world on each mutation."""
from __future__ import annotations
from .support import Backend, _record
_put = Backend.put
def put(self, document_id, version, text, metadata):
    _record("rebuild", "*")
    return _put(self, document_id, version, text, metadata)
Backend.put = put
