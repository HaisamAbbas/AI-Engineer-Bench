"""Prepare a non-official MVP-2 release manifest after all technical gates pass.

The command deliberately refuses the current development catalog. It cannot
create a release-ready artifact from placeholder labels or self-reported
fixtures.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aieb_core.bugfinding_v2 import BugReleaseManifest, BugTaskRevision
from scripts.validate_bugfinding_track import CATALOG, ROOT, audit


def prepare(release_id: str, output: Path) -> BugReleaseManifest:
    report = audit()
    if not report["controls_complete"]:
        raise RuntimeError("MVP-2 release refused: repository controls are incomplete")
    if not report["hidden_label_workflow_complete"] or not report["execution_evidence_complete"]:
        raise RuntimeError("MVP-2 release refused: hidden labels and execution evidence are incomplete")
    if report["independent_review"] != "approved":
        raise RuntimeError("MVP-2 release refused: independent review is not approved")
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    tasks: list[BugTaskRevision] = []
    for entry in catalog["tasks"]:
        manifest_path = ROOT / entry["manifest_path"]
        tasks.append(BugTaskRevision.model_validate(json.loads(manifest_path.read_text(encoding="utf-8"))))
    if not tasks:
        raise RuntimeError("MVP-2 release refused: catalog has no tasks")
    manifest = BugReleaseManifest(
        schema_version="aieb.bug-release/v2",
        release_id=release_id,
        cohort_id=tasks[0].cohort_id,
        mode=tasks[0].mode,
        tasks=tuple(tasks),
        status="ready-for-review",
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    # Pydantic remains the source of truth for the release-id pattern.
    prepare(args.release_id, args.output)
    print(json.dumps({"status": "ready-for-review", "output": str(args.output)}))
