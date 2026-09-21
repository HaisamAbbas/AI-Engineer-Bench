"""Trusted RAG-05 evaluator that exercises a candidate only over HTTP.

The held-out corpus is real upstream sqlite-utils README text (see fixture.py).
Portability: this module is ALSO executed inside the Harbor verifier container
(tests/fixtures/rag05_model_agent), where the aieb_runner package is not
installed, so the one aieb_runner import is a documented stdlib fallback.
"""

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

from .fixture import DELETED_DOCUMENT, UPDATED_DOCUMENT, generate

try:
    from aieb_runner.lifecycle import CandidateUnavailableError as _CandidateUnavailable
except ImportError:  # verifier-container fallback; behavior is identical

    class _CandidateUnavailable(RuntimeError):
        pass


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
    # Containerized verifier startup includes Python import and process
    # scheduling overhead. A fixed two-second poll window caused valid
    # candidates to be classified as unavailable before answering /health.
    STARTUP_TIMEOUT_SECONDS = 15.0

    def __init__(self, repo: Path, ledger_url: str) -> None:
        self.repo = repo
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        environment = dict(os.environ)
        environment["AIEB_LEDGER_URL"] = ledger_url
        self.process = subprocess.Popen([sys.executable, "-m", "knowledge_service.server", "--port", str(self.port)], cwd=repo, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

    def __enter__(self) -> "CandidateService":
        deadline = time.monotonic() + self.STARTUP_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                detail = self.process.stderr.read() if self.process.stderr is not None else ""
                if self.process.stderr is not None:
                    self.process.stderr.close()
                raise _CandidateUnavailable(f"candidate service stopped: {detail}")
            try:
                status, _ = _request(self.base, "GET", "/health")
                if status == 200:
                    return self
            except (urllib.error.URLError, ConnectionError):
                # ConnectionError covers http.client.RemoteDisconnected (a
                # connection accepted and then dropped without a response,
                # e.g. a candidate that dies mid-startup); the next poll()
                # iteration then surfaces its stderr as CandidateUnavailable.
                time.sleep(0.05)
        detail = self.process.stderr.read() if self.process.stderr is not None else ""
        raise _CandidateUnavailable(
            "candidate service did not become ready within "
            f"{self.STARTUP_TIMEOUT_SECONDS:.1f}s"
            + (f": {detail.strip()}" if detail.strip() else "")
        )

    def __exit__(self, *args: object) -> None:
        self.process.terminate()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=3)
        if self.process.stderr is not None:
            self.process.stderr.close()
        time.sleep(0.08)


def evaluate(repo: Path, *, seed: int = 5309) -> dict[str, object]:
    """Run a fresh live candidate process and return requirement-keyed outcomes."""
    fixture = generate(seed)
    documents = fixture["documents"]
    assert isinstance(documents, dict)
    python_api = documents[UPDATED_DOCUMENT]["metadata"]
    assert isinstance(python_api, dict)
    cli_group = documents["cli-csv-insert"]["metadata"]
    assert isinstance(cli_group, dict)
    checks: dict[str, bool] = {}
    diagnostics: dict[str, str] = {}

    def search(service: "CandidateService", query: str, metadata: dict[str, object] | None = None) -> list[dict[str, Any]]:
        status, response = _request(service.base, "POST", "/search", {"query": query, "top_k": 10, "metadata": metadata})
        assert status == 200, f"search failed: {status} {response}"
        hits = response["hits"]
        assert isinstance(hits, list)
        return hits

    with Ledger() as ledger, CandidateService(repo, ledger.url) as service:
        status, health = _request(service.base, "GET", "/health")
        checks["api-ready"] = status == 200 and health == {"status": "ready"}

        ingestion_order = fixture["ingestion_order"]
        assert isinstance(ingestion_order, list)
        for document_id in ingestion_order:
            item = documents[str(document_id)]
            assert isinstance(item, dict)
            _request(service.base, "POST", "/documents", {"id": document_id, **item})

        before_update = len(ledger.events)
        status, _ = _request(service.base, "POST", "/documents", {"id": UPDATED_DOCUMENT, "version": 2, "text": fixture["replacement_text"], "metadata": python_api})
        update_hits = search(service, "python library", python_api)
        checks["latest-version-visible"] = status == 201 and len(update_hits) == 1 and update_hits[0].get("id") == UPDATED_DOCUMENT and update_hits[0].get("version") == 2 and update_hits[0].get("text") == fixture["replacement_text"]
        checks["stale-version-absent"] = not search(service, "import sqlite_utils", python_api)
        checks["citation-version-mapping"] = checks["latest-version-visible"] and update_hits[0].get("chunk_id") == f"{UPDATED_DOCUMENT}:2:0"
        new_events = ledger.events[before_update:]
        checks["incremental-write-scope"] = bool(new_events) and all(event.get("document_id") == UPDATED_DOCUMENT and event.get("operation") != "rebuild" for event in new_events)

        status, _ = _request(service.base, "POST", "/documents", {"id": UPDATED_DOCUMENT, "version": 2, "text": fixture["replacement_text"], "metadata": python_api})
        checks["idempotent-event-replay"] = status == 200 and len(search(service, "python library", python_api)) == 1

        v1 = documents[UPDATED_DOCUMENT]
        assert isinstance(v1, dict)
        status, _ = _request(service.base, "POST", "/documents", {"id": UPDATED_DOCUMENT, "version": 1, "text": v1["text"], "metadata": python_api})
        checks["lower-version-rejected"] = status == 200 and len(search(service, "python library", python_api)) == 1 and not search(service, "import sqlite_utils", python_api)

        status, _ = _request(service.base, "POST", "/documents", {"id": UPDATED_DOCUMENT, "version": 2, "text": documents["insert-all"]["text"], "metadata": python_api})
        checks["equal-version-conflict"] = status == 409

        dogs_hits = search(service, "dogs", cli_group)
        checks["metadata-filter-respected"] = sorted(str(hit["id"]) for hit in dogs_hits) == ["cli-csv-insert", "cli-tables-counts"] and all(hit["version"] == 1 for hit in dogs_hits)

        analytics_hits = search(service, "family personal analytics")
        checks["full-text-match"] = len(analytics_hits) == 1 and analytics_hits[0].get("id") == "dogsheep-analytics" and analytics_hits[0].get("version") == 1

        deleted_item = documents[DELETED_DOCUMENT]
        assert isinstance(deleted_item, dict)
        status, _ = _request(service.base, "DELETE", f"/documents/{DELETED_DOCUMENT}", {"version": 1})
        absent_after_delete = not search(service, "custom sql", python_api)
        status, _ = _request(service.base, "POST", "/documents", {"id": DELETED_DOCUMENT, "version": 1, "text": deleted_item["text"], "metadata": python_api})
        checks["deleted-content-absent"] = absent_after_delete and not search(service, "custom sql", python_api)

        status, _ = _request(service.base, "POST", "/documents", {"id": DELETED_DOCUMENT, "version": 2, "text": fixture["recreation_text"], "metadata": python_api})
        recreation_hits = search(service, "exporting postgresql", python_api)
        checks["higher-version-recreation"] = status == 201 and len(recreation_hits) == 1 and recreation_hits[0].get("version") == 2 and recreation_hits[0].get("chunk_id") == f"{DELETED_DOCUMENT}:2:0"

        control_hits = search(service, "exploring")
        control = documents["datasette-explore"]
        assert isinstance(control, dict)
        checks["unaffected-documents-preserved"] = len(control_hits) == 1 and control_hits[0].get("id") == "datasette-explore" and control_hits[0].get("version") == 1 and control_hits[0].get("text") == control["text"]

    for requirement, passed in checks.items():
        if not passed:
            diagnostics[requirement] = "published HTTP contract outcome did not match the held-out fixture"
    return {"fixture_version": fixture["fixture_version"], "seed": seed, "checks": checks, "diagnostics": diagnostics, "pass": all(checks.values())}
