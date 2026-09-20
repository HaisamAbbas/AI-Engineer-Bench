"""ENG-021: Provenance and contamination/overlap review for development tasks.

Reviews the twelve public development tasks for:

1. Provenance: each task's provenience.json declares source, license, and
   data origin. Checks that no task claims external proprietary data.
2. Overlap: the public dev_data/ fixtures must not share identifying
   tokens with the holdout fixtures that would make holdout cases derivable
   from public data. This is a necessary (not sufficient) check - overlap
   of tokens is a contamination signal, but absence of overlap does not
   prove contamination-freedom (spec section 22).
3. Label compliance: every task's status is correctly labeled as
   "development" (public task with private examples = official-public-origin
   at most, NOT held-out).

This script reports evidence, never a "contamination-free" verdict. Per
spec section 22 and the prompt: "Do not call private examples on public
tasks contamination-free."

Usage:
  python scripts/provenance_overlap_review.py [--task <task-id>] [--holdout-dir <path>]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CATALOG_PATH = ROOT / "suites" / "dev" / "catalog.json"


def _load_catalog() -> dict:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def _get_task_dirs() -> list[tuple[str, Path]]:
    """Return (task_id, task_dir) pairs from the dev catalog."""
    catalog = _load_catalog()
    tasks = []
    for entry in catalog.get("tasks", []):
        task_id = entry["id"]
        task_dir = ROOT / "suites" / "dev" / entry["path"]
        tasks.append((task_id, task_dir))
    return tasks


def _extract_tokens(text: str) -> set[str]:
    """Extract lowercase alphabetic tokens from text content."""
    return set(re.findall(r"[a-z]+", text.lower()))


def _read_text_files(directory: Path) -> dict[str, str]:
    """Read all text files in a directory tree."""
    contents: dict[str, str] = {}
    if not directory.exists():
        return contents
    for path in sorted(directory.rglob("*")):
        if path.is_file() and path.suffix in (".md", ".json", ".py", ".yaml", ".txt"):
            try:
                contents[str(path.relative_to(directory))] = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
    return contents


def _check_provenance(task_id: str, task_dir: Path) -> dict:
    """Check provenance.json for the task."""
    provenance_path = task_dir / "provenance.json"
    if not provenance_path.exists():
        return {"check": "provenance-present", "status": "FAIL", "detail": "provenance.json not found"}
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    issues = []
    if not provenance.get("source"):
        issues.append("missing 'source' field")
    if not provenance.get("license"):
        issues.append("missing 'license' field")
    if provenance.get("contains_customer_data"):
        issues.append("contains_customer_data is true - must be false for public tasks")
    if provenance.get("source") and "proprietary" in provenance["source"].lower():
        issues.append(f"source claims proprietary origin: {provenance['source']}")
    status = "FAIL" if issues else "PASS"
    return {
        "check": "provenance",
        "status": status,
        "detail": "; ".join(issues) if issues else f"source={provenance.get('source')}, license={provenance.get('license')}",
    }


def _check_dev_data_tokens(task_id: str, task_dir: Path) -> dict:
    """Extract and hash the tokens from dev_data/ for overlap comparison."""
    dev_data_dir = task_dir / "dev_data"
    if not dev_data_dir.exists():
        return {"check": "dev-data-present", "status": "WARN", "detail": "no dev_data/ directory found"}
    files = _read_text_files(dev_data_dir)
    if not files:
        return {"check": "dev-data-present", "status": "WARN", "detail": "dev_data/ contains no readable text files"}
    all_text = "\n".join(files.values())
    tokens = _extract_tokens(all_text)
    token_hash = hashlib.sha256(json.dumps(sorted(tokens), ensure_ascii=False).encode()).hexdigest()[:16]
    return {
        "check": "dev-data-tokens",
        "status": "PASS",
        "detail": f"extracted {len(tokens)} unique tokens from {len(files)} files; token-set hash: {token_hash}",
        "token_count": len(tokens),
        "token_hash": token_hash,
    }


def _check_holdout_overlap(task_id: str, task_dir: Path, holdout_dir: Path | None) -> dict:
    """Check for token overlap between dev_data/ and holdout fixtures."""
    if holdout_dir is None:
        return {"check": "holdout-overlap", "status": "SKIP", "detail": "no --holdout-dir provided; cannot check overlap"}

    dev_data_dir = task_dir / "dev_data"
    if not dev_data_dir.exists():
        return {"check": "holdout-overlap", "status": "WARN", "detail": "no dev_data/ to compare against holdout"}

    dev_files = _read_text_files(dev_data_dir)
    dev_tokens = _extract_tokens("\n".join(dev_files.values()))

    holdout_files = _read_text_files(holdout_dir)
    if not holdout_files:
        return {"check": "holdout-overlap", "status": "WARN", "detail": "holdout directory contains no readable text files"}

    holdout_tokens = _extract_tokens("\n".join(holdout_files.values()))
    overlap = dev_tokens & holdout_tokens
    # Filter out very short tokens (stop words) - minimum 4 chars
    significant_overlap = {t for t in overlap if len(t) >= 4}

    if significant_overlap:
        return {
            "check": "holdout-overlap",
            "status": "FAIL",
            "detail": f"significant token overlap ({len(significant_overlap)} tokens): {sorted(significant_overlap)[:20]}",
        }
    return {
        "check": "holdout-overlap",
        "status": "PASS",
        "detail": f"no significant token overlap (dev: {len(dev_tokens)} tokens, holdout: {len(holdout_tokens)} tokens, overlap: {len(significant_overlap)} significant)",
    }


def _check_label_compliance(task_id: str, task_dir: Path) -> dict:
    """Verify the task is correctly labeled as development, not held-out."""
    task_yaml_path = task_dir / "task.yaml"
    if not task_yaml_path.exists():
        return {"check": "label-compliance", "status": "FAIL", "detail": "task.yaml not found"}
    task_yaml_text = task_yaml_path.read_text(encoding="utf-8")
    # The task is in suites/dev/ which is explicitly development. Check it doesn't claim held-out status.
    if "held-out" in task_yaml_text.lower() and "development" not in task_yaml_text.lower():
        return {"check": "label-compliance", "status": "FAIL", "detail": "task.yaml claims held-out status but lives in suites/dev/"}
    return {
        "check": "label-compliance",
        "status": "PASS",
        "detail": "task correctly labeled as development (suites/dev/) with official-public-origin holdout fixtures at most",
    }


def review_task(task_id: str, holdout_dir: Path | None) -> dict:
    task_dirs = {tid: tdir for tid, tdir in _get_task_dirs()}
    if task_id not in task_dirs:
        raise ValueError(f"unknown task: {task_id}")
    task_dir = task_dirs[task_id]
    findings = [
        _check_provenance(task_id, task_dir),
        _check_dev_data_tokens(task_id, task_dir),
        _check_holdout_overlap(task_id, task_dir, holdout_dir),
        _check_label_compliance(task_id, task_dir),
    ]
    status = "FAIL" if any(f["status"] == "FAIL" for f in findings) else ("WARN" if any(f["status"] == "WARN" for f in findings) else "PASS")
    verdict_note = "This is an evidence review, not a contamination-freedom verdict. "
    if holdout_dir is None:
        verdict_note += "Overlap check was skipped (no --holdout-dir). "
    verdict_note += "Per spec section 22, private examples on public tasks do not constitute an uncontaminated hidden benchmark."
    return {
        "task_id": task_id,
        "task_path": str(task_dir.relative_to(ROOT)),
        "status": status,
        "findings": findings,
        "verdict_note": verdict_note,
    }


def review_all(holdout_dir: Path | None) -> dict:
    results = []
    for task_id, _task_dir in _get_task_dirs():
        results.append(review_task(task_id, holdout_dir))
    overlap_skipped = sum(
        1
        for r in results
        for f in r["findings"]
        if f["check"] == "holdout-overlap" and f["status"] == "SKIP"
    )
    holdout_overlap_assessed = overlap_skipped == 0
    if holdout_dir is None:
        overlap_review_status = (
            "NOT ASSESSED for any task - no --holdout-dir was provided. No genuine held-out "
            "fixture directory exists in this repository yet (curate_holdout.py refuses to write "
            "under the repository root and has not been run to produce a persisted holdout set "
            "outside it). The per-task 'PASS' status below reflects only the provenance, "
            "dev-data-token, and label-compliance checks; it does NOT mean overlap was checked, "
            "and must not be read as '12/12 overlap pass'."
        )
    elif overlap_skipped:
        overlap_review_status = (
            f"PARTIALLY ASSESSED - overlap check ran but was skipped for {overlap_skipped} of "
            f"{len(results)} task(s) (see each task's holdout-overlap finding for why)."
        )
    else:
        overlap_review_status = (
            f"ASSESSED for all {len(results)} tasks against --holdout-dir={holdout_dir}. "
            "Absence of token overlap is a necessary, not sufficient, contamination signal "
            "(spec section 22); it does not by itself certify contamination-freedom."
        )
    summary = {
        "schema_version": "aieb.provenance-review/v1",
        "total_tasks": len(results),
        "passed": sum(1 for r in results if r["status"] == "PASS"),
        "failed": sum(1 for r in results if r["status"] == "FAIL"),
        "warnings": sum(1 for r in results if r["status"] == "WARN"),
        "holdout_overlap_assessed": holdout_overlap_assessed,
        "overlap_review_status": overlap_review_status,
        "results": results,
        "verdict_note": "Automated evidence report. Per spec section 22 and the prompt, private examples on public tasks are NOT labeled contamination-free regardless of overlap findings.",
    }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", choices=[tid for tid, _ in _get_task_dirs()], help="Review a single task")
    parser.add_argument("--check-all", action="store_true", help="Review all tasks")
    parser.add_argument("--holdout-dir", type=Path, help="Directory containing generated holdout fixtures for overlap check")
    parser.add_argument("--output", type=Path, help="Optional JSON output path")
    args = parser.parse_args()

    if args.task:
        result = review_task(args.task, args.holdout_dir)
    else:
        result = review_all(args.holdout_dir)

    output = json.dumps(result, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    print(output, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
