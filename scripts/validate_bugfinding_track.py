"""Fail-closed audit for the separate MVP-2 bug-finding catalog.

This validates development task packages and reports which release gates are
still missing. It never treats a public placeholder as a hidden label and
never marks a task admitted without private evaluator evidence and review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from aieb_core.bugfinding_v2 import BugTaskRevision

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "suites" / "mvp2" / "catalog.json"
ZERO_DIGEST = "0" * 64


def _tree_digest(root: Path) -> str:
    entries: list[bytes] = []
    for path in sorted(
        candidate
        for candidate in root.rglob("*")
        if candidate.is_file() and "__pycache__" not in candidate.parts
    ):
        entries.append(path.relative_to(root).as_posix().encode() + b"\0" + path.read_bytes())
    return hashlib.sha256(b"\0".join(entries)).hexdigest()


def _safe_repo_path(value: str) -> Path | None:
    candidate = (ROOT / value).resolve()
    try:
        candidate.relative_to(ROOT.resolve())
    except ValueError:
        return None
    return candidate


def _private_ref(value: object) -> bool:
    return isinstance(value, str) and value.startswith("private://")


def _audit_task(entry: dict[str, object]) -> dict[str, object]:
    errors: list[str] = []
    manifest_name = entry.get("manifest_path")
    if not isinstance(manifest_name, str):
        return {"task_id": entry.get("task_id"), "status": "invalid", "errors": ["missing manifest_path"]}
    manifest_path = _safe_repo_path(manifest_name)
    if manifest_path is None or not manifest_path.is_file():
        return {"task_id": entry.get("task_id"), "status": "invalid", "errors": ["manifest path is missing or escapes repository"]}
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        task = BugTaskRevision.model_validate(raw)
    except Exception as exc:
        return {"task_id": entry.get("task_id"), "status": "invalid", "errors": [f"manifest validation failed: {exc}"]}
    if task.task_id != entry.get("task_id"):
        errors.append("catalog task_id does not match the immutable task manifest")
    if task.mode != entry.get("mode"):
        errors.append("catalog mode does not match the task manifest")
    task_root = manifest_path.parent
    repo = task_root / "repo"
    if not repo.is_dir():
        errors.append("fixed repository directory is missing")
    elif _tree_digest(repo) != task.repository.content_digest:
        errors.append("repository content digest does not match the frozen snapshot")
    required_files = {
        "baseline": task.baseline_ref,
        "reference": task.reference_ref,
        "alternative": task.alternative_ref,
        "negative_control": task.negative_control_ref,
        "public_tests": raw.get("public_test_ref"),
    }
    for label, relative in required_files.items():
        if not isinstance(relative, str) or not relative or not (task_root / relative).exists():
            errors.append(f"missing {label} control/reference")
    if not (task_root / "counterexamples").is_dir():
        errors.append("missing adversarial/shortcut controls")
    if not _private_ref(raw.get("hidden_label_ref")):
        errors.append("hidden_label_ref must be private:// and access-controlled")
    if raw.get("hidden_label_digest") in (None, ZERO_DIGEST):
        errors.append("hidden-label digest is a placeholder; no private label set is bound")
    if not _private_ref(raw.get("evaluator_ref")):
        errors.append("evaluator_ref must be private:// and evaluator-isolated")
    if not task.public_examples:
        errors.append("public examples are missing")
    if not raw.get("execution_evidence_ref"):
        errors.append("no Harbor/evaluator end-to-end evidence is recorded")
    return {
        "task_id": task.task_id,
        "revision": task.revision,
        "mode": task.mode,
        "cohort_id": task.cohort_id,
        "repository_digest": task.repository.content_digest,
        "license_id": task.repository.license_id,
        "status": "admission-candidate" if not errors else "blocked",
        "errors": errors,
        "private_labels_present": not any("hidden-label" in error for error in errors),
        "execution_evidence_present": not any("end-to-end" in error for error in errors),
    }


def audit() -> dict[str, object]:
    raw = json.loads(CATALOG.read_text(encoding="utf-8"))
    errors: list[str] = []
    if raw.get("track") != "mvp2-public-repository-bug-finding":
        errors.append("catalog is not an MVP-2 bug-finding catalog")
    if raw.get("status") != "development-only":
        errors.append("catalog must remain development-only")
    if raw.get("official_release_eligible"):
        errors.append("MVP-2 catalog cannot claim official eligibility")
    entries = raw.get("tasks", [])
    if not isinstance(entries, list) or not entries:
        errors.append("catalog has no repository task revisions")
        entries = []
    task_reports = [_audit_task(entry) for entry in entries if isinstance(entry, dict)]
    task_ids = [str(report.get("task_id")) for report in task_reports]
    if len(set(task_ids)) != len(task_ids):
        errors.append("catalog contains duplicate task identities")
    modes = {str(report.get("mode")) for report in task_reports if report.get("mode")}
    if len(modes) > 1:
        errors.append("finding-only and patch tasks must use separate catalogs/cohorts")
    blocked = [report for report in task_reports if report.get("status") != "admission-candidate"]
    all_controls = bool(task_reports) and not blocked and not errors
    return {
        "schema_version": "aieb.mvp2-bug-track-audit/v2",
        "track": raw.get("track"),
        "cohort_id": raw.get("cohort_id"),
        "catalog_status": "blocked" if errors or blocked else "ready-for-independent-review",
        "schema_validated": not errors,
        "validated": not errors and not blocked,
        "tasks": task_reports,
        "tasks_admitted": 0,
        "controls_complete": all_controls,
        "hidden_label_workflow_complete": False,
        "execution_evidence_complete": all(
            report.get("execution_evidence_present") for report in task_reports
        ) if task_reports else False,
        "independent_review": "pending",
        "official_release_eligible": False,
        "errors": errors,
        "reason": (
            "Development-only MVP-2 audit; private labels, end-to-end evaluator evidence, "
            "independent review, and authorized publication remain pending."
        ),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    rendered = json.dumps(audit(), indent=2, sort_keys=True) + "\n"
    print(rendered, end="")
    if args.output:
        output = args.output if args.output.is_absolute() else ROOT / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
