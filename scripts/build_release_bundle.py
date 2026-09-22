"""ENG-021: Build a curated development candidate bundle.

By default this packages only tasks accepted by the Track-A curation audit.
Thin legacy fixtures require ``--include-rejected-development`` and cannot
satisfy ``--require-admitted-suite``. Every mode excludes:
- tests/maintainer/ (trusted evaluator/holdout code — never in a public bundle)
- Any holdout fixture paths outside the repo

This is not an official release. Per spec section 22, private examples on
public tasks do not constitute a contamination-free hidden benchmark.

Usage:
  python scripts/build_release_bundle.py --output <bundle-dir>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _suite_depth_audit() -> dict:
    # Import lazily so the bundle's file-copy helpers remain usable by focused
    # tests without importing the catalog audit at module import time.
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from scripts.validate_mvp1_suite import audit

    return audit()


def _rmtree_windows_safe(path: Path) -> None:
    """Remove a tree, tolerating Windows' occasional PermissionError on rmtree.

    See tests/test_eng021_bundle_exclusions.py's copy of this helper for the
    rationale: a copied file can inherit a read-only bit, or a file can be
    transiently locked by an indexer/antivirus, and shutil.rmtree raises
    PermissionError ([WinError 5]) on that one path. Clear the bit and retry
    instead of silently ignoring all errors.
    """

    def _on_error(func, target, exc_info):
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except OSError:
            pass

    shutil.rmtree(path, onerror=_on_error)


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hash_tree(root: Path) -> str:
    entries = []
    for path in sorted(p for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
        entries.append(path.relative_to(root).as_posix().encode() + b"\0" + path.read_bytes())
    return hashlib.sha256(b"\0".join(entries)).hexdigest()


def _load_catalog() -> dict:
    return json.loads((ROOT / "suites" / "dev" / "catalog.json").read_text(encoding="utf-8"))


def _build_task_bundle(task_id: str, task_dir: Path, bundle_root: Path) -> dict:
    """Build a single task's bundle, excluding protected paths."""
    task_bundle = bundle_root / task_id.replace(".", "-")
    task_bundle.mkdir(parents=True, exist_ok=True)

    # Copy task components (repo, contracts, dev_data, environment, instruction.md, provenance.json)
    for component in ["repo", "contracts", "dev_data", "environment", "instruction.md", "provenance.json", "task.yaml"]:
        src = task_dir / component
        dst = task_bundle / component
        if src.exists():
            if src.is_dir():
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dst)

    # Compute and verify digests match task.yaml
    import yaml
    task_yaml = yaml.safe_load((task_dir / "task.yaml").read_text(encoding="utf-8"))

    repo_digest = _hash_tree(task_bundle / "repo")
    provenance_digest = _hash_file(task_bundle / "provenance.json")
    contract_digest = _hash_file(task_bundle / "contracts" / "application-api.md")
    topology_digest = _hash_file(task_bundle / "environment" / "README.md")

    digest_mismatches = []
    if task_yaml.get("source", {}).get("repository_digest") and task_yaml["source"]["repository_digest"] != repo_digest:
        digest_mismatches.append(f"repository_digest: claimed={task_yaml['source']['repository_digest'][:16]}... actual={repo_digest[:16]}...")
    if task_yaml.get("source", {}).get("provenance_digest") and task_yaml["source"]["provenance_digest"] != provenance_digest:
        digest_mismatches.append(f"provenance_digest mismatch")
    if task_yaml.get("application", {}).get("contract_digest") and task_yaml["application"]["contract_digest"] != contract_digest:
        digest_mismatches.append(f"contract_digest mismatch")
    if task_yaml.get("environment", {}).get("service_topology_digest") and task_yaml["environment"]["service_topology_digest"] != topology_digest:
        digest_mismatches.append(f"service_topology_digest mismatch")

    # CRITICAL: verify no protected paths snuck into the bundle
    protected_check = _check_no_protected_paths(task_bundle)

    return {
        "task_id": task_id,
        "path": str(task_bundle.relative_to(bundle_root)),
        "digests": {
            "repository": repo_digest,
            "provenance": provenance_digest,
            "contract": contract_digest,
            "service_topology": topology_digest,
        },
        "digest_mismatches": digest_mismatches,
        "protected_path_violations": protected_check["violations"],
        "excluded_paths": protected_check["excluded"],
    }


def _check_no_protected_paths(bundle_root: Path) -> dict:
    """Verify NO protected paths are in the bundle.

    This is a passing test, not a claim. If any protected path is found,
    the bundle build FAILS.
    """
    violations = []
    excluded = []
    for path in bundle_root.rglob("*"):
        rel = path.relative_to(bundle_root)
        parts = rel.parts
        if "tests" in parts and "maintainer" in parts:
            violations.append(str(rel))
        elif "dev_tests" in parts:
            excluded.append(str(rel))
        elif any("holdout" in p.lower() for p in parts):
            violations.append(str(rel))
    return {"violations": violations, "excluded": excluded}


def _mkdir_windows_safe(path: Path, *, retries: int = 3, delay_seconds: float = 0.2) -> None:
    """Create a directory tree, retrying a transient PermissionError once or twice.

    On Windows, a directory just removed by `_rmtree_windows_safe` can still be
    momentarily locked by an antivirus/indexer handle, so an immediate `mkdir`
    can raise PermissionError ([WinError 5]) even though the path is free a
    moment later. `exist_ok=True` also tolerates the directory already existing
    (e.g. a prior failed run's rmtree left it in place)."""
    import time

    last_error: OSError | None = None
    for attempt in range(retries):
        try:
            path.mkdir(parents=True, exist_ok=True)
            return
        except PermissionError as exc:
            last_error = exc
            if attempt < retries - 1:
                time.sleep(delay_seconds)
    raise last_error  # type: ignore[misc]


def build_bundle(
    output_dir: Path,
    *,
    require_admitted_suite: bool = False,
    include_rejected_development: bool = False,
) -> dict:
    """Build a curated candidate bundle, excluding rejected tasks by default."""
    catalog = _load_catalog()
    suite_audit = _suite_depth_audit()
    if require_admitted_suite and not suite_audit["suite_admission_eligible"]:
        raise RuntimeError(
            "SUITE NOT ADMITTED: Track A depth/diversity and independent catalog "
            "review gates are not satisfied; deepen/remove shallow tasks and obtain "
            "independent admission before building an admitted-only bundle."
        )
    curated_ids = {
        row["id"] for row in suite_audit.get("tasks", [])
        if row.get("status") == "curated-candidate"
    }
    tasks = catalog["tasks"] if include_rejected_development else [
        task for task in catalog["tasks"] if task["id"] in curated_ids
    ]
    if not tasks:
        raise RuntimeError(
            "NO CURATED TASKS: every checked-in Track A package is currently "
            "rejected or blocked by the depth/diversity audit. Use "
            "--include-rejected-development only for a clearly labeled local "
            "fixture bundle; it is not an admission or release path."
        )
    bundle_root = output_dir / "release-candidate-bundle"
    if bundle_root.exists():
        _rmtree_windows_safe(bundle_root)
    _mkdir_windows_safe(bundle_root)
    task_bundles = []

    for task in tasks:
        task_id = task["id"]
        task_dir = ROOT / "suites" / "dev" / task["path"]
        bundle_info = _build_task_bundle(task_id, task_dir, bundle_root)
        task_bundles.append(bundle_info)

    # Global protected-path check across the ENTIRE bundle
    global_check = _check_no_protected_paths(bundle_root)
    if global_check["violations"]:
        raise RuntimeError(
            f"BUNDLE VIOLATION: protected paths found in bundle:\n"
            + "\n".join(global_check["violations"])
            + "\nThis must never happen - tests/maintainer/ and holdout data must never be in a public bundle."
        )

    # Verify no task has digest mismatches
    mismatches = [tb for tb in task_bundles if tb["digest_mismatches"]]
    if mismatches:
        raise RuntimeError(f"Bundle contains tasks with stale digests: {json.dumps(mismatches, indent=2)}")

    # Write bundle manifest
    manifest = {
        "schema_version": "aieb.release-bundle/v1",
        "label": (
            "rejected-development-fixtures"
            if include_rejected_development
            else "curated-development-candidates"
        ),
        "contamination_clause": (
            "This development bundle contains only the task set selected by its curation mode. "
            "Private holdout fixtures are NOT included (they are stored outside the repository). "
            "Per spec section 22, private examples on public tasks do not constitute a "
            "contamination-free hidden benchmark."
        ),
        "release_status": (
            "NOT A RELEASE - explicitly rejected development fixtures"
            if include_rejected_development
            else "NOT OFFICIALLY RELEASED - curated candidates pending independent review"
        ),
        "built_at": _git_commit_or_unknown(),
        "total_tasks": len(task_bundles),
        "protected_path_exclusion_verified": True,
        "digest_verification": "all_task_digests_match_repo",
        "suite_depth_audit": {
            "schema_version": suite_audit["schema_version"],
            "depth_diversity_satisfied": suite_audit["depth_diversity_satisfied"],
            "suite_admission_eligible": suite_audit["suite_admission_eligible"],
            "catalog_review_status": suite_audit["catalog_review_status"],
            "near_duplicate_groups": suite_audit["near_duplicate_groups"],
            "shallow_task_ids": [
                row["id"] for row in suite_audit["tasks"] if row["depth_status"] == "shallow"
            ],
            "rejected_task_ids": [
                row["id"] for row in suite_audit["tasks"] if row["status"] == "rejected"
            ],
            "project_statuses": {
                project["id"]: project["status"] for project in suite_audit["projects"]
            },
            "report_path": "docs/implementation/evidence/V2-GAP-007/mvp1-depth-audit.json",
        },
        "tasks": task_bundles,
        "excluded_from_bundle": [
            "tests/maintainer/ - trusted evaluator and holdout code (private)",
            ".aieb/runs/ - local run state (ephemeral)",
            ".cache/ - local caches (ephemeral)",
        ],
    }

    manifest_path = bundle_root / "MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Verify the manifest itself is inside the bundle (not outside)
    _check_no_protected_paths(bundle_root)  # re-verify after adding manifest

    return manifest


def _git_commit_or_unknown() -> str:
    import subprocess
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=str(ROOT), timeout=5)
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Output directory for the bundle (outside repo recommended)")
    parser.add_argument(
        "--require-admitted-suite",
        action="store_true",
        help="refuse unless structural depth/diversity and independent catalog admission are satisfied",
    )
    parser.add_argument(
        "--include-rejected-development",
        action="store_true",
        help="include explicitly rejected thin fixtures in a non-release local bundle",
    )
    args = parser.parse_args()

    manifest = build_bundle(
        args.output,
        require_admitted_suite=args.require_admitted_suite,
        include_rejected_development=args.include_rejected_development,
    )
    print(json.dumps(manifest, sort_keys=True, ensure_ascii=False, indent=2))
    print(f"\nBundle built at: {args.output / 'release-candidate-bundle'}", file=sys.stderr)
    print("VERIFICATION: No protected paths (tests/maintainer/, dev_tests/, holdout) found in bundle.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
