"""Shared control-candidate implementation; copied only with counterexamples."""
from __future__ import annotations
import json, os, urllib.request
def _record(operation, document_id):
    endpoint = os.environ.get("AIEB_LEDGER_URL")
    if endpoint:
        request = urllib.request.Request(endpoint, data=json.dumps({"operation": operation, "document_id": document_id}).encode(), headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(request, timeout=2).read()
class Backend:
    def __init__(self): self.current = {}
    def put(self, document_id, version, text, metadata):
        old = self.current.get(document_id)
        if old:
            if version < old["version"] or (version == old["version"] and old["deleted"]): return 200, {"status":"ignored", "version":old["version"]}
            if version == old["version"]:
                return (200, {"status":"idempotent", "version":version}) if old["text"] == text and old["metadata"] == (metadata or {}) else (409, {"status":"conflict", "version":version})
        self.current[document_id] = {"version":version, "text":text, "metadata":metadata or {}, "deleted":False}; _record("replace", document_id); return 201, {"status":"stored", "version":version}
    def delete(self, document_id, version):
        old=self.current.get(document_id)
        if old and version < old["version"]: return 200, {"status":"ignored", "version":old["version"]}
        self.current[document_id]={"version":version, "text":None, "metadata":{}, "deleted":True}; _record("tombstone", document_id); return 200, {"status":"deleted", "version":version}
    def search(self, query, top_k, metadata):
        hits=[]
        for document_id, item in sorted(self.current.items()):
            if item["deleted"] or query.casefold() not in str(item["text"]).casefold(): continue
            if metadata and any(item["metadata"].get(k) != v for k,v in metadata.items()): continue
            hits.append({"id":document_id,"version":item["version"],"chunk_id":f"{document_id}:{item['version']}:0","text":item["text"]})
        return hits[:top_k]
