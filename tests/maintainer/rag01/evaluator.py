"""Trusted RAG-01 evaluator that exercises a candidate only over HTTP."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .fixture import generate


class _LedgerHandler(BaseHTTPRequestHandler):
    events: list[dict[str, Any]] = []

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_POST(self) -> None:
        size = int(self.headers.get("Content-Length", "0"))
        value = json.loads(self.rfile.read(size))
        if not isinstance(value, dict):
            self.send_error(400)
            return
        self.events.append(value)
        self.send_response(204)
        self.end_headers()


class Ledger:
    def __enter__(self) -> "Ledger":
        _LedgerHandler.events = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _LedgerHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/writes"
        return self

    @property
    def events(self) -> list[dict[str, Any]]:
        return list(_LedgerHandler.events)

    def __exit__(self, *args: object) -> None:
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _request(base: str, method: str, path: str, body: dict[str, object] | None = None) -> tuple[int, dict[str, Any]]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


class CandidateService:
    def __init__(self, repo: Path, ledger_url: str) -> None:
        self.repo = repo
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        environment = dict(os.environ)
        environment["AIEB_LEDGER_URL"] = ledger_url
        self.process = subprocess.Popen([sys.executable, "-m", "knowledge_service.server", "--port", str(self.port)], cwd=repo, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

    def __enter__(self) -> "CandidateService":
        for _ in range(50):
            if self.process.poll() is not None:
                detail = self.process.stderr.read() if self.process.stderr is not None else ""
                if self.process.stderr is not None:
                    self.process.stderr.close()
                raise RuntimeError(f"candidate service stopped: {detail}")
            try:
                status, _ = _request(self.base, "GET", "/health")
                if status == 200:
                    return self
            except urllib.error.URLError:
                time.sleep(0.04)
        self.process.terminate()
        self.process.wait(timeout=3)
        if self.process.stderr is not None:
            self.process.stderr.close()
        raise RuntimeError("candidate service did not become ready")

    def __exit__(self, *args: object) -> None:
        self.process.terminate()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=3)
        if self.process.stderr is not None:
            self.process.stderr.close()


def _search(service: CandidateService, query: str, metadata: dict[str, object] | None = None) -> list[dict[str, Any]]:
    body: dict[str, object] = {"query": query, "top_k": 10}
    if metadata:
        body["metadata"] = metadata
    status, value = _request(service.base, "POST", "/search", body)
    return value.get("hits", []) if status == 200 else []


def evaluate(repo: Path, *, seed: int = 4107) -> dict[str, object]:
    """Run a fresh live candidate process and return requirement-keyed outcomes."""
    fixture = generate(seed)
    documents = fixture["documents"]
    assert isinstance(documents, dict)
    checks: dict[str, bool] = {}
    diagnostics: dict[str, str] = {}
    with Ledger() as ledger, CandidateService(repo, ledger.url) as service:
        status, health = _request(service.base, "GET", "/health")
        checks["api-ready"] = status == 200 and health == {"status": "ready"}
        for document_id, item in documents.items():
            assert isinstance(item, dict)
            _request(service.base, "POST", "/documents", {"id": document_id, **item})

        before_update = len(ledger.events)
        amber = documents["amber"]
        assert isinstance(amber, dict)
        status, _ = _request(service.base, "POST", "/documents", {"id": "amber", "version": 2, "text": fixture["replacement_text"], "metadata": amber["metadata"]})
        violet_hits = _search(service, "violet", {"group": "updates"})
        checks["latest-version-visible"] = status == 201 and len(violet_hits) == 1 and violet_hits[0].get("id") == "amber" and violet_hits[0].get("version") == 2 and violet_hits[0].get("text") == fixture["replacement_text"]
        checks["citation-version-mapping"] = checks["latest-version-visible"] and violet_hits[0].get("chunk_id") == "amber:2:0"
        new_events = ledger.events[before_update:]
        checks["incremental-unaffected-write-scope"] = bool(new_events) and all(event.get("document_id") == "amber" and event.get("operation") != "rebuild" for event in new_events)

        status, _ = _request(service.base, "POST", "/documents", {"id": "amber", "version": 2, "text": fixture["replacement_text"], "metadata": amber["metadata"]})
        checks["idempotent-event-replay"] = status == 200 and len(_search(service, "violet")) == 1
        status, _ = _request(service.base, "POST", "/documents", {"id": "amber", "version": 1, "text": "stale amber", "metadata": amber["metadata"]})
        checks["lower-version-rejected"] = status == 200 and len(_search(service, "violet")) == 1 and not _search(service, "stale amber")
        status, _ = _request(service.base, "POST", "/documents", {"id": "amber", "version": 2, "text": "conflicting amber", "metadata": amber["metadata"]})
        checks["equal-version-conflict"] = status == 409

        _request(service.base, "DELETE", "/documents/bravo", {"version": 2})
        _request(service.base, "POST", "/documents", {"id": "bravo", "version": 1, "text": "stale bravo", "metadata": {"group": "deletes"}})
        checks["deleted-content-absent"] = not _search(service, "removable") and not _search(service, "stale bravo")
        status, _ = _request(service.base, "POST", "/documents", {"id": "bravo", "version": 3, "text": fixture["recreation_text"], "metadata": {"group": "deletes"}})
        recreation_hits = _search(service, "recreation")
        checks["higher-version-recreation"] = status == 201 and len(recreation_hits) == 1 and recreation_hits[0].get("version") == 3 and recreation_hits[0].get("chunk_id") == "bravo:3:0"
        cedar_hits = _search(service, "unaffected", {"group": "control"})
        checks["unaffected-documents-preserved"] = len(cedar_hits) == 1 and cedar_hits[0].get("id") == "cedar" and cedar_hits[0].get("version") == 1

    for requirement, passed in checks.items():
        if not passed:
            diagnostics[requirement] = "published HTTP contract outcome did not match the held-out fixture"
    return {"fixture_version": fixture["fixture_version"], "seed": seed, "checks": checks, "diagnostics": diagnostics, "pass": all(checks.values())}
