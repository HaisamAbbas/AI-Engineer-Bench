"""Harbor verifier for the RAG-05 real-repo model-agent fixture.

Overlays the candidate's submitted workspace onto a fresh directory, then
runs the trusted RAG-05 evaluator (maintainer-owned snapshot under /tests/
rag05/) against it over HTTP. Exit 0 = reward 1. The evaluator is stdlib-only
by construction, so no aieb_runner install is needed in this container.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

# The AIEB egress guard injects HTTP_PROXY/HTTPS_PROXY into this container with
# NO_PROXY="" ("nothing bypasses it via NO_PROXY"), which also captures
# loopback calls: urllib would send http://127.0.0.1:<port> requests to the
# host-side guard proxy, which cannot reach this container's loopback. This
# verifier makes no external calls - everything it drives is local - so drop
# the proxy environment for this process and its children (the candidate
# service it spawns, including the candidate's local write-ledger posts).
for _proxy_key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_proxy_key, None)
os.environ["NO_PROXY"] = "127.0.0.1,localhost"
os.environ["no_proxy"] = "127.0.0.1,localhost"

sys.path.insert(0, "/tests")

from rag05.evaluator import evaluate  # noqa: E402


def main() -> int:
    submission = Path("/workspace/submission")
    required = ("knowledge_service", "sqlite_utils", "sqlite_fts4")
    missing = [name for name in required if not (submission / name).is_dir()]
    if missing:
        print(json.dumps({"pass": False, "error": "submission missing packages", "missing": missing}))
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp) / "repo"
        workspace.mkdir(parents=True, exist_ok=True)
        # The agent's /workspace/submission contains the full candidate (vendored
        # sqlite_utils package, its dependency closure, the patched
        # knowledge_service, etc.). Copy those CONTENTS into workspace so the
        # server's cwd=workspace can import them directly. A naive
        # shutil.copytree(submission, workspace) would nest a submission/
        # subdirectory and break the server's imports.
        _ignore = shutil.ignore_patterns("model_track_summary.json")
        for entry in submission.iterdir():
            dst = workspace / entry.name
            if entry.is_dir():
                shutil.copytree(entry, dst, ignore=_ignore, dirs_exist_ok=True)
            else:
                shutil.copy2(entry, dst)
        outcome = evaluate(workspace)
        print(json.dumps(outcome, sort_keys=True))
        return 0 if outcome["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
