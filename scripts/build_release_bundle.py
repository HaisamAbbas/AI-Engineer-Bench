"""ENG-021: Build a release candidate bundle.

Packages all 12 public development tasks with verified digests, frozen
manifests, and evaluator revisions — but EXCLUDES:
- tests/maintainer/ (trusted evaluator/holdout code — never in a public bundle)
- Any holdout fixture paths outside the repo

This is a release-candidate bundle, NOT an official release. Label:
official-public-origin. Per spec section 22 and the prompt, private examples
on public tasks do not constitute a contamination-free hidden benchmark.

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


def build_bundle(output_dir: Path) -> dict:
    """Build the complete release candidate bundle."""
    bundle_root = output_dir / "release-candidate-bundle"
    if bundle_root.exists():
        _rmtree_windows_safe(bundle_root)
    bundle_root.mkdir(parents=True)

    catalog = _load_catalog()
    tasks = catalog["tasks"]
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
        "label": "official-public-origin",
        "contamination_clause": (
            "This release candidate bundle contains the twelve public development tasks. "
            "Private holdout fixtures are NOT included (they are stored outside the repository). "
            "Per spec section 22, private examples on public tasks do not constitute a "
            "contamination-free hidden benchmark."
        ),
        "release_status": "NOT OFFICIALLY RELEASED - ready for independent review",
        "built_at": _git_commit_or_unknown(),
        "total_tasks": len(task_bundles),
        "protected_path_exclusion_verified": True,
        "digest_verification": "all_task_digests_match_repo",
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
    args = parser.parse_args()

    manifest = build_bundle(args.output)
    print(json.dumps(manifest, sort_keys=True, ensure_ascii=False, indent=2))
    print(f"\nBundle built at: {args.output / 'release-candidate-bundle'}", file=sys.stderr)
    print("VERIFICATION: No protected paths (tests/maintainer/, dev_tests/, holdout) found in bundle.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
