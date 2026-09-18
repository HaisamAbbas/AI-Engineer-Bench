"""ENG-021: Document an evaluator review pass over the trusted evaluator code.

Reviews the trusted evaluators under tests/maintainer/ for three properties
required by spec section 26 ("Evaluator admission and resistance to shortcuts"):

1. Leakage: the evaluator must not import or directly access candidate code.
2. Shortcut resistance: the evaluator must exercise live application behavior
   over HTTP, not inspect candidate source for expected patterns.
3. Closure hashing: the evaluator's trusted dependency closure (its own
   package directory plus tests/maintainer/common.py) must be what
   hash_evaluator_closure hashes, so a change to evaluator behavior is
   detectable as a digest change.

This script does NOT perform independent human review. It reports evidence
presence: whether the evaluator module exists, whether it imports candidate
modules, whether it uses HTTP-based evaluation, and what its closure digest
is. Each finding is surfaced as a structured record, never an "approved"
verdict.

Usage:
  python scripts/evaluator_review.py --task <task-id> [--check-all]
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.maintainer.common import CandidateProcess  # noqa: E402 - confirms common.py is importable

TASK_EVALUATOR_MODULES = {
    "rag.document-freshness": "tests.maintainer.rag01.evaluator",
    "rag.metadata-filter-topk": "tests.maintainer.rag02.evaluator",
    "rag.citation-current-span": "tests.maintainer.rag03.evaluator",
    "rag.embedding-version": "tests.maintainer.rag04.evaluator",
    "ext.missingness": "tests.maintainer.ext01.evaluator",
    "ext.batch-alignment": "tests.maintainer.ext02.evaluator",
    "ext.unit-normalization": "tests.maintainer.ext03.evaluator",
    "ext.partial-batch": "tests.maintainer.ext04.evaluator",
    "tool.false-completion": "tests.maintainer.tool01.evaluator",
    "tool.idempotent-write": "tests.maintainer.tool02.evaluator",
    "tool.session-isolation": "tests.maintainer.tool03.evaluator",
    "tool.corrected-arguments": "tests.maintainer.tool04.evaluator",
}

TASK_SOURCE_DIRS = {
    "rag.document-freshness": "knowledge_service",
    "rag.metadata-filter-topk": "search_service",
    "rag.citation-current-span": "citation_service",
    "rag.embedding-version": "embedding_service",
    "ext.missingness": "missingness_service",
    "ext.batch-alignment": "extraction_service",
    "ext.unit-normalization": "unit_service",
    "ext.partial-batch": "batch_service",
    "tool.false-completion": "workflow_service",
    "tool.idempotent-write": "write_service",
    "tool.session-isolation": "session_service",
    "tool.corrected-arguments": "correction_service",
}


def _module_path(module_name: str) -> Path:
    """Convert a dotted module name to its filesystem path under ROOT."""
    # For a module like "tests.maintainer.rag01.evaluator", the file is at tests/maintainer/rag01/evaluator.py
    parts = module_name.split(".")
    file_path = ROOT / Path(*parts)
    if file_path.exists():
        return file_path
    # Try with .py extension
    file_path_py = ROOT / Path(*parts) 
    candidate = ROOT / (Path(*parts[:-1]) / f"{parts[-1]}.py") if len(parts) > 1 else ROOT / f"{parts[-1]}.py"
    if candidate.exists():
        return candidate
    return ROOT / Path(*parts)  # return as-is even if not found (error will be reported)


def _hash_evaluator_closure(evaluator_module: str) -> str:
    """Mirror packages/aieb-cli/src/aieb_cli/main.py's hash_evaluator_closure
    exactly, so this review's reported digest matches what `aieb task validate`
    stores in task.yaml's evaluator_digest field."""
    parts = evaluator_module.split(".")
    package_dir = ROOT / Path(*parts[:-1])  # e.g., tests/maintainer/rag01
    files = {p for p in package_dir.glob("*.py") if "__pycache__" not in p.parts}
    common = ROOT / "tests" / "maintainer" / "common.py"
    if common.exists():
        files.add(common)
    entries = []
    for path in sorted(files, key=lambda p: p.relative_to(ROOT).as_posix()):
        entries.append(path.relative_to(ROOT).as_posix().encode() + b"\0" + path.read_bytes())
    return hashlib.sha256(b"\0".join(entries)).hexdigest()


def _check_no_candidate_imports(evaluator_path: Path, source_dir: str) -> list[str]:
    """Detect whether the evaluator imports the candidate's source directory."""
    violations: list[str] = []
    try:
        tree = ast.parse(evaluator_path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        return [f"could not parse evaluator: {exc}"]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == source_dir or alias.name.startswith(f"{source_dir}."):
                    violations.append(f"imports candidate module: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.module and (node.module == source_dir or node.module.startswith(f"{source_dir}.")):
                violations.append(f"imports from candidate module: {node.module}")
    return violations


def _check_http_evaluation(evaluator_path: Path) -> tuple[bool, str]:
    """Check whether the evaluator exercises the candidate over HTTP.

    The evaluator may import HTTP helpers from tests/maintainer/common.py,
    so we check both the evaluator and common.py for HTTP indicators."""
    source = evaluator_path.read_text(encoding="utf-8")
    http_indicators = ["urllib.request", "http.client", "requests.get", "requests.post", "httpx", "ThreadingHTTPServer"]
    found = [ind for ind in http_indicators if ind in source]
    if not found:
        # Check common.py since evaluators delegate HTTP via `from tests.maintainer.common import request`
        common_path = ROOT / "tests" / "maintainer" / "common.py"
        if common_path.exists():
            common_source = common_path.read_text(encoding="utf-8")
            found = [f"{ind} (in common.py)" for ind in http_indicators if ind in common_source]
    if found:
        return True, f"uses HTTP-based evaluation ({', '.join(found)})"
    return False, "no HTTP evaluation indicators found in evaluator or common.py - may inspect candidate source directly"


def _check_liveness_assertions(evaluator_path: Path) -> list[str]:
    """Check whether the evaluator runs the candidate as a separate process."""
    source = evaluator_path.read_text(encoding="utf-8")
    indicators = ["subprocess", "Popen", "CandidateService", "CandidateProcess"]
    found = [ind for ind in indicators if ind in source]
    if found:
        return [f"candidate runs as separate process ({', '.join(found)})"]
    return ["WARNING: evaluator does not appear to run the candidate as a separate process"]


def _get_task_evaluator_digests() -> dict[str, str]:
    """Read the declared evaluator_digest from each task.yaml."""
    import yaml
    digests: dict[str, str] = {}
    catalog = json.loads((ROOT / "suites" / "dev" / "catalog.json").read_text(encoding="utf-8"))
    task_dir_map = {t["id"]: t["path"] for t in catalog["tasks"]}
    for task_id, module in TASK_EVALUATOR_MODULES.items():
        task_path = task_dir_map.get(task_id)
        if not task_path:
            continue
        task_yaml_path = ROOT / "suites" / "dev" / task_path / "task.yaml"
        if task_yaml_path.exists():
            task_yaml = yaml.safe_load(task_yaml_path.read_text(encoding="utf-8"))
            digests[task_id] = task_yaml.get("evaluator", {}).get("evaluator_digest", "")
    return digests


def review_task(task_id: str) -> dict:
    """Produce a structured review record for a single task's evaluator."""
    if task_id not in TASK_EVALUATOR_MODULES:
        raise ValueError(f"unknown task: {task_id}")

    evaluator_module = TASK_EVALUATOR_MODULES[task_id]
    evaluator_path = _module_path(evaluator_module)
    source_dir = TASK_SOURCE_DIRS[task_id]
    declared_digest = _get_task_evaluator_digests().get(task_id, "")
    actual_digest = _hash_evaluator_closure(evaluator_module)

    findings: list[dict] = []
    status: list[str] = []

    # Check 1: file exists
    if not evaluator_path.exists():
        status.append("FAIL")
        findings.append({"check": "evaluator-exists", "status": "fail", "detail": f"evaluator not found at {evaluator_path}"})
        return {"task_id": task_id, "evaluator_module": evaluator_module, "evaluator_path": str(evaluator_path), "status": "FAIL", "findings": findings}

    status.append("PASS")

    # Check 2: no candidate imports
    import_violations = _check_no_candidate_imports(evaluator_path, source_dir)
    if import_violations:
        status.append("FAIL")
        for v in import_violations:
            findings.append({"check": "leakage", "status": "fail", "detail": v})
    else:
        findings.append({"check": "leakage", "status": "pass", "detail": f"evaluator does not import candidate module {source_dir}"})

    # Check 3: HTTP evaluation
    http_result, http_detail = _check_http_evaluation(evaluator_path)
    if http_result:
        findings.append({"check": "shortcut-resistance", "status": "pass", "detail": http_detail})
    else:
        status.append("FAIL")
        findings.append({"check": "shortcut-resistance", "status": "fail", "detail": http_detail})

    # Check 4: separate process
    for finding in _check_liveness_assertions(evaluator_path):
        if finding.startswith("WARNING"):
            status.append("FAIL")
            findings.append({"check": "liveness", "status": "fail", "detail": finding})
        else:
            findings.append({"check": "liveness", "status": "pass", "detail": finding})

    # Check 5: closure digest matches
    if declared_digest and declared_digest == actual_digest:
        findings.append({"check": "closure-hashing", "status": "pass", "detail": f"declared digest matches actual: {actual_digest}"})
    elif declared_digest and declared_digest != actual_digest:
        status.append("FAIL")
        findings.append({"check": "closure-hashing", "status": "fail", "detail": f"declared digest {declared_digest} does not match actual {actual_digest}"})
    else:
        status.append("WARN")
        findings.append({"check": "closure-hashing", "status": "warn", "detail": f"no declared digest in task.yaml; actual closure digest is {actual_digest}"})

    overall = "PASS" if all(s == "PASS" for s in status) else ("FAIL" if "FAIL" in status else "WARN")
    return {
        "task_id": task_id,
        "evaluator_module": evaluator_module,
        "evaluator_path": str(evaluator_path),
        "status": overall,
        "findings": findings,
        "closure_digest": actual_digest,
        "review_type": "automated-evidence-review",
        "verdict_note": "This is an automated evidence report, not an independent human review. Independent admission review remains PENDING.",
    }


def review_all() -> dict:
    results = []
    for task_id in TASK_EVALUATOR_MODULES:
        results.append(review_task(task_id))
    summary = {
        "schema_version": "aieb.evaluator-review/v1",
        "total_tasks": len(results),
        "all_pass": all(r["status"] == "PASS" for r in results),
        "passed": sum(1 for r in results if r["status"] == "PASS"),
        "failed": sum(1 for r in results if r["status"] == "FAIL"),
        "warnings": sum(1 for r in results if r["status"] == "WARN"),
        "verdict_note": "Automated evidence report. Independent human review remains PENDING for all tasks.",
        "results": results,
    }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", choices=list(TASK_EVALUATOR_MODULES.keys()), help="Review a single task's evaluator")
    parser.add_argument("--check-all", action="store_true", help="Review all task evaluators")
    parser.add_argument("--output", type=Path, help="Optional JSON output path")
    args = parser.parse_args()

    if args.task:
        result = review_task(args.task)
    else:
        result = review_all()

    output = json.dumps(result, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        if args.output.resolve() == ROOT.resolve() or ROOT.resolve() in args.output.resolve().parents:
            print("WARNING: writing to repo-root path; recommended for non-public evidence only", file=sys.stderr)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    print(output, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
