"""Public self-check for the corpus service: run `python dev_tests/selfcheck.py`.

Mirrors the published HTTP contract over toy data. The trusted evaluator uses
a held-out real-text corpus and additional maintainer-only checks.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

# In-container runs have HTTP_PROXY/HTTPS_PROXY injected by the benchmark's
# egress guard (with NO_PROXY="") - urllib would then route these loopback
# 127.0.0.1 calls through a host-side proxy that cannot reach this process.
# Everything this self-check does is local, so drop the proxy environment.
for _proxy_key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_proxy_key, None)
os.environ["NO_PROXY"] = "127.0.0.1,localhost"
os.environ["no_proxy"] = "127.0.0.1,localhost"

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from knowledge_service.server import Handler  # noqa: E402


def _call(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        BASE + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def _search(query: str, metadata: dict | None = None, top_k: int = 10) -> list[dict]:
    return _call("POST", "/search", {"query": query, "top_k": top_k, "metadata": metadata})[1]["hits"]


def main() -> int:
    results: dict[str, bool] = {}

    def check(name: str, value: bool) -> None:
        results[name] = bool(value)

    status, health = _call("GET", "/health")
    check("api-ready", status == 200 and health == {"status": "ready"})

    _call("POST", "/documents", {"id": "planets", "version": 1, "text": "jupiter giant planet", "metadata": {"group": "space"}})
    _call("POST", "/documents", {"id": "plants", "version": 1, "text": "fern green plant", "metadata": {"group": "garden"}})

    status, _ = _call("POST", "/documents", {"id": "planets", "version": 2, "text": "saturn ringed planet", "metadata": {"group": "space"}})
    hits = _search("planet")
    check("latest-version-visible", status == 201 and len(hits) == 1 and hits[0]["id"] == "planets" and hits[0]["version"] == 2 and hits[0]["text"] == "saturn ringed planet")
    check("stale-version-absent", not _search("jupiter"))

    status, _ = _call("POST", "/documents", {"id": "planets", "version": 2, "text": "saturn ringed planet", "metadata": {"group": "space"}})
    check("idempotent-event-replay", status == 200 and len(_search("planet")) == 1)

    status, _ = _call("POST", "/documents", {"id": "planets", "version": 1, "text": "jupiter giant planet", "metadata": {"group": "space"}})
    check("lower-version-rejected", status == 200 and len(_search("planet")) == 1 and not _search("jupiter"))

    status, _ = _call("POST", "/documents", {"id": "planets", "version": 2, "text": "neptune windy planet", "metadata": {"group": "space"}})
    check("equal-version-conflict", status == 409)

    filtered = _search("plant", {"group": "garden"})
    check("metadata-filter-respected", len(filtered) == 1 and filtered[0]["id"] == "plants")

    status, _ = _call("DELETE", "/documents/plants", {"version": 1})
    check("deleted-content-absent", status == 200 and not _search("fern") and not _search("plant"))

    status, _ = _call("POST", "/documents", {"id": "plants", "version": 2, "text": "rose thorny flower", "metadata": {"group": "garden"}})
    hits = _search("flower")
    check("higher-version-recreation", status == 201 and len(hits) == 1 and hits[0]["id"] == "plants" and hits[0]["version"] == 2 and hits[0]["chunk_id"] == "plants:2:0")

    untouched = _search("ringed")
    check("unaffected-documents-preserved", len(untouched) == 1 and untouched[0]["id"] == "planets" and untouched[0]["version"] == 2)

    failed = [name for name, passed in results.items() if not passed]
    for name, passed in results.items():
        print(f"{'PASS' if passed else 'FAIL'} {name}")
    print(f"dev_tests: {len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    BASE = f"http://127.0.0.1:{server.server_port}"
    try:
        raise SystemExit(main())
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
