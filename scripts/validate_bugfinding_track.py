"""Fail-closed structural audit for the separate MVP-2 bug track."""
from __future__ import annotations

import json
from pathlib import Path

from aieb_core.bugfinding_v2 import BugReleaseManifest

ROOT = Path(__file__).resolve().parents[1]


def audit() -> dict[str, object]:
    raw = json.loads((ROOT / "suites/mvp2/catalog.json").read_text(encoding="utf-8"))
    if raw.get("track") != "mvp2-public-repository-bug-finding":
        raise ValueError("catalog is not an MVP-2 bug-finding catalog")
    if raw.get("status") != "development-only" or raw.get("official_release_eligible"):
        raise ValueError("MVP-2 catalog must remain development-only")
    return {**raw, "validated": True, "tasks_admitted": 0, "independent_review": "pending"}


if __name__ == "__main__":
    print(json.dumps(audit(), indent=2, sort_keys=True))
