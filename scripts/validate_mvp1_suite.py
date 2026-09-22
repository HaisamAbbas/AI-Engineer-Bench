"""Audit the development catalog against the v2 MVP-1 depth requirements.

This is a fail-closed audit, not an admission or release command. It identifies
shallow/blocked tasks instead of declaring synthetic variants to be genuine
applications.
"""

from __future__ import annotations

import json
import ast
import hashlib
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "suites" / "dev" / "catalog.json"

# Structural pre-admission floors. These deliberately exceed what a one-file
# HTTP fixture can satisfy. They are a rejection pre-screen, never proof that a
# task is semantically difficult or independently admitted.
MIN_SUITE_TASKS = 12
MAX_SUITE_TASKS = 20
MIN_DOMAIN_SOURCE_FILES = 2
MIN_DOMAIN_LOGIC_LINES = 40
MIN_DOMAIN_SYMBOLS = 4
MIN_DOMAIN_BRANCH_POINTS = 4
MIN_PROJECT_LOGIC_LINES = 180
MIN_PROJECT_TASKS = 3
MIN_PROJECT_DEEP_TASKS = 2
NEAR_DUPLICATE_THRESHOLD = 0.92

EXCLUDED_SOURCE_PARTS = frozenset({
    "__pycache__", "alternative", "build", "counterexamples", "dev_data",
    "dev_tests", "dist", "fixtures", "generated", "migrations", "reference",
    "test", "tests", "vendor", ".venv",
})
EXCLUDED_SOURCE_NAMES = frozenset({"__init__.py", "server.py"})

REQUIRED_BY_CATEGORY = {
    # A suite is deep when its tasks collectively cover the family. Requiring
    # every capability on every task incorrectly labels focused, complementary
    # tasks as shallow. Each task must declare and demonstrate its own focus.
    "rag": ("index", "retriev"),
    "extraction": ("schema",),
    "tool_app": ("state",),
}


def _domain_files(task: Path) -> list[Path]:
    """Return authored application files, excluding padding-prone material."""

    return sorted(
        path
        for path in (task / "repo").rglob("*.py")
        if path.name not in EXCLUDED_SOURCE_NAMES
        and not EXCLUDED_SOURCE_PARTS.intersection(path.relative_to(task / "repo").parts)
        and not path.is_symlink()
    )


def _normalised_source(path: Path) -> str:
    """Canonicalize syntax to detect copied application skeletons."""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    class Normalizer(ast.NodeTransformer):
        def visit_Name(self, node: ast.Name) -> ast.AST:
            node.id = "NAME"
            return self.generic_visit(node)

        def visit_arg(self, node: ast.arg) -> ast.AST:
            node.arg = "ARG"
            return self.generic_visit(node)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
            node.name = "FUNC"
            return self.generic_visit(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
            node.name = "FUNC"
            return self.generic_visit(node)

        def visit_ClassDef(self, node: ast.ClassDef) -> ast.AST:
            node.name = "CLASS"
            return self.generic_visit(node)

        def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
            node.attr = "ATTR"
            return self.generic_visit(node)

        def visit_Constant(self, node: ast.Constant) -> ast.AST:
            if not isinstance(node.value, (bool, type(None))):
                node.value = f"<{type(node.value).__name__}>"
            return node

    return ast.dump(
        Normalizer().visit(tree),
        annotate_fields=False,
        include_attributes=False,
    )


def _logic_lines(path: Path) -> int:
    """Count statement start lines, not multiline spans or comments.

    Counting ``end_lineno`` ranges allowed a multiline string/call padded with
    blank or comment lines to increase the old score. One physical start line
    is counted per executable statement line instead.
    """

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    covered: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.stmt):
            continue
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            continue
        start = getattr(node, "lineno", None)
        if start is not None:
            covered.add(start)
    return len(covered)


def _source_metrics(task: Path) -> dict[str, object]:
    files = _domain_files(task)
    normalised = "\n".join(_normalised_source(path) for path in files)
    symbols = 0
    branch_points = 0
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        symbols += sum(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            for node in ast.walk(tree)
        )
        branch_points += sum(
            isinstance(node, (
                ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.Match,
                ast.IfExp, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp,
            ))
            for node in ast.walk(tree)
        )
    return {
        "domain_source_files": len(files),
        "domain_logic_lines": sum(_logic_lines(path) for path in files),
        "domain_symbols": symbols,
        "domain_branch_points": branch_points,
        "normalised_fingerprint": hashlib.sha256(normalised.encode("utf-8")).hexdigest(),
        "normalised_source": normalised,
    }


def _task_path(entry: dict[str, object]) -> Path:
    suite_root = (ROOT / "suites" / "dev").resolve()
    relative = entry.get("path")
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("catalog task path must be a non-empty relative path")
    task = (suite_root / relative).resolve(strict=True)
    try:
        task.relative_to(suite_root)
    except ValueError as exc:
        raise ValueError("catalog task path escapes suites/dev") from exc
    if not task.is_dir():
        raise ValueError("catalog task path must resolve to a directory")
    return task


def audit() -> dict[str, object]:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    rows: list[dict[str, object]] = []
    normalised_sources: dict[str, str] = {}
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    catalog_errors: list[str] = []
    for entry in catalog.get("tasks", []):
        task_id = str(entry.get("id", ""))
        path_key = str(entry.get("path", ""))
        if not task_id or task_id in seen_ids:
            catalog_errors.append(f"duplicate-or-blank-task-id:{task_id or '<blank>'}")
        if not path_key or path_key in seen_paths:
            catalog_errors.append(f"duplicate-or-blank-task-path:{path_key or '<blank>'}")
        seen_ids.add(task_id)
        seen_paths.add(path_key)
        try:
            task = _task_path(entry)
        except (OSError, ValueError) as exc:
            catalog_errors.append(f"{task_id or '<blank>'}:invalid-path:{exc}")
            continue
        task_yaml = (task / "task.yaml").read_text(encoding="utf-8").lower()
        contract = (task / "contracts" / "application-api.md").read_text(encoding="utf-8").lower()
        metadata_path = task / "mvp1.yaml"
        metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
        if not isinstance(metadata, dict):
            metadata = {}
        text = task_yaml + "\n" + contract + "\n" + yaml.safe_dump(metadata).lower() + "\n" + entry["id"].lower()
        task_manifest = yaml.safe_load((task / "task.yaml").read_text(encoding="utf-8"))
        category_name = str(task_manifest.get("category", "unknown")) if isinstance(task_manifest, dict) else "unknown"
        required = REQUIRED_BY_CATEGORY.get(category_name, ())
        missing_depth = [term for term in required if term not in text]
        covered = metadata.get("covered_capabilities", [])
        if not isinstance(covered, list) or len(covered) < 2:
            missing_depth.append("at-least-two-covered-capabilities")
        elif any(str(cap).lower() not in text for cap in covered):
            missing_depth.append("declared-capability-not-in-contract")
        missing_package = [name for name in ("instruction.md", "reference", "alternative", "counterexamples", "dev_tests", "environment") if not (task / name).exists()]
        required_metadata = (
            "ticket", "application_surface", "hidden_cases", "regression_requirements",
            "operational_edge_case", "shortcut_controls", "reference", "baseline",
            "alternative", "engineering_mechanism", "change_surface", "difficulty_rationale",
        )
        missing_metadata = [name for name in required_metadata if not metadata.get(name)]
        source = _source_metrics(task)
        normalised_sources[task_id] = str(source["normalised_source"])
        depth_findings: list[str] = []
        if int(source["domain_source_files"]) < MIN_DOMAIN_SOURCE_FILES:
            depth_findings.append(f"domain-source-files<{MIN_DOMAIN_SOURCE_FILES}")
        if int(source["domain_logic_lines"]) < MIN_DOMAIN_LOGIC_LINES:
            depth_findings.append(f"domain-logic-lines<{MIN_DOMAIN_LOGIC_LINES}")
        if int(source["domain_symbols"]) < MIN_DOMAIN_SYMBOLS:
            depth_findings.append(f"domain-symbols<{MIN_DOMAIN_SYMBOLS}")
        if int(source["domain_branch_points"]) < MIN_DOMAIN_BRANCH_POINTS:
            depth_findings.append(f"domain-branch-points<{MIN_DOMAIN_BRANCH_POINTS}")
        depth_status = "deep-enough-for-review" if not depth_findings else "shallow"
        curation = entry.get("curation")
        curation = curation if isinstance(curation, dict) else {}
        curation_decision = curation.get("decision")
        curation_reason = curation.get("reason")
        if curation_decision not in {"accept", "reject"}:
            catalog_errors.append(f"{task_id}:missing-or-invalid-curation-decision")
        if not isinstance(curation_reason, str) or not curation_reason.strip():
            catalog_errors.append(f"{task_id}:missing-curation-reason")
        structural_candidate = not missing_depth and not missing_package and not missing_metadata and not depth_findings
        if curation_decision == "reject":
            status = "rejected"
        elif structural_candidate:
            status = "curated-candidate"
        else:
            status = "blocked-candidate"
        project = entry.get("application_project")
        if not isinstance(project, str) or not project.strip():
            catalog_errors.append(f"{task_id}:missing-application-project")
            project = "unknown"
        rows.append({
            "id": task_id,
            "family_id": entry.get("family_id"),
            "category": category_name,
            "status": status,
            "curation_decision": curation_decision,
            "curation_reason": curation_reason,
            "missing_depth_indicators": missing_depth,
            "missing_package_parts": missing_package,
            "missing_mvp1_metadata": missing_metadata,
            "application_project": project,
            "engineering_mechanism": metadata.get("engineering_mechanism"),
            "change_surface": metadata.get("change_surface"),
            "domain_source_files": source["domain_source_files"],
            "domain_logic_lines": source["domain_logic_lines"],
            "domain_symbols": source["domain_symbols"],
            "domain_branch_points": source["domain_branch_points"],
            "depth_status": depth_status,
            "depth_findings": depth_findings,
            "normalised_fingerprint": source["normalised_fingerprint"],
            "independent_review": metadata.get("independent_review", "pending"),
            # Catalog metadata is not an authority for admission. A future
            # signed/database-derived export must populate this audit through a
            # separate verifier; editing catalog.json can never self-admit.
            "admission_state": "not-verified",
            "official": False,
        })
    curated = [row for row in rows if row["status"] == "curated-candidate"]
    rejected = [row for row in rows if row["status"] == "rejected"]
    categories = {row["category"] for row in curated}
    catalog_categories = {row["category"] for row in rows}
    project_rows: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        project_rows[str(row["application_project"])].append(row)
    projects: list[dict[str, object]] = []
    for project, members in sorted(project_rows.items()):
        candidates = [row for row in members if row["status"] == "curated-candidate"]
        fingerprints = {str(row["normalised_fingerprint"]) for row in candidates}
        mechanisms = {str(row["engineering_mechanism"]) for row in candidates if row["engineering_mechanism"]}
        deep_tasks = [row for row in candidates if row["depth_status"] == "deep-enough-for-review"]
        project_findings: list[str] = []
        if len(candidates) < MIN_PROJECT_TASKS:
            project_findings.append(f"task-count<{MIN_PROJECT_TASKS}")
        if sum(int(row["domain_logic_lines"]) for row in candidates) < MIN_PROJECT_LOGIC_LINES:
            project_findings.append(f"aggregate-domain-logic-lines<{MIN_PROJECT_LOGIC_LINES}")
        if len(deep_tasks) < MIN_PROJECT_DEEP_TASKS:
            project_findings.append(f"deep-task-count<{MIN_PROJECT_DEEP_TASKS}")
        if len(mechanisms) != len(candidates):
            project_findings.append("engineering-mechanisms-not-unique")
        projects.append({
            "id": project,
            "catalog_task_ids": [str(row["id"]) for row in members],
            "task_ids": [str(row["id"]) for row in candidates],
            "rejected_task_ids": [str(row["id"]) for row in members if row["status"] == "rejected"],
            "task_count": len(candidates),
            "aggregate_domain_logic_lines": sum(int(row["domain_logic_lines"]) for row in candidates),
            "deep_task_count": len(deep_tasks),
            "distinct_normalised_implementations": len(fingerprints),
            "distinct_engineering_mechanisms": len(mechanisms),
            "status": "defensible-project" if not project_findings else "shallow-or-incomplete",
            "findings": project_findings,
        })
    duplicate_groups: list[list[str]] = []
    for index, left in enumerate(curated):
        for right in curated[index + 1 :]:
            # Tasks from the same application are expected to share much of a
            # repository. Cross-project similarity is what undermines the
            # claim of three genuinely different applications.
            if left["application_project"] == right["application_project"]:
                continue
            similarity = SequenceMatcher(
                None,
                normalised_sources[str(left["id"])],
                normalised_sources[str(right["id"])],
            ).ratio()
            if similarity >= NEAR_DUPLICATE_THRESHOLD:
                duplicate_groups.append([str(left["id"]), str(right["id"])])
    suite_size_satisfied = MIN_SUITE_TASKS <= len(curated) <= MAX_SUITE_TASKS
    depth_satisfied = bool(curated) and all(row["depth_status"] == "deep-enough-for-review" for row in curated)
    project_diversity_satisfied = (
        len([project for project in projects if project["task_count"]]) >= 3
        and all(project["status"] == "defensible-project" for project in projects if project["task_count"])
        and not duplicate_groups
    )
    category_diversity_satisfied = categories == set(REQUIRED_BY_CATEGORY)
    admission_evidence_verified = False
    depth_diversity_satisfied = (
        suite_size_satisfied and depth_satisfied and project_diversity_satisfied
        and category_diversity_satisfied and not catalog_errors
    )
    blockers: list[str] = []
    if catalog_errors:
        blockers.append("catalog-integrity-errors")
    if not suite_size_satisfied:
        blockers.append(f"curated-task-count-not-in-{MIN_SUITE_TASKS}-{MAX_SUITE_TASKS}")
    if not category_diversity_satisfied:
        blockers.append("curated-category-diversity-incomplete")
    if not project_diversity_satisfied:
        blockers.append("curated-project-diversity-incomplete")
    if not depth_satisfied:
        blockers.append("curated-task-depth-incomplete")
    if not admission_evidence_verified:
        blockers.append("verified-admission-export-unavailable")
    return {
        "schema_version": "aieb.mvp1-suite-audit/v2",
        "catalog_review_status": catalog.get("review_status", "unknown"),
        "curation_policy": catalog.get("curation_policy"),
        "catalog_task_count": len(rows),
        "curated_candidate_count": len(curated),
        "rejected_task_count": len(rejected),
        "catalog_errors": catalog_errors,
        "category_count": len(categories),
        "catalog_category_count": len(catalog_categories),
        "required_categories": sorted(REQUIRED_BY_CATEGORY),
        "catalog_category_coverage_satisfied": catalog_categories == set(REQUIRED_BY_CATEGORY),
        "category_diversity_satisfied": category_diversity_satisfied,
        "suite_size_satisfied": suite_size_satisfied,
        "tasks": rows,
        "projects": projects,
        "near_duplicate_groups": duplicate_groups,
        "depth_policy": {
            "min_suite_tasks": MIN_SUITE_TASKS,
            "max_suite_tasks": MAX_SUITE_TASKS,
            "min_domain_source_files": MIN_DOMAIN_SOURCE_FILES,
            "min_domain_logic_lines": MIN_DOMAIN_LOGIC_LINES,
            "min_domain_symbols": MIN_DOMAIN_SYMBOLS,
            "min_domain_branch_points": MIN_DOMAIN_BRANCH_POINTS,
            "min_project_logic_lines": MIN_PROJECT_LOGIC_LINES,
            "min_project_tasks": MIN_PROJECT_TASKS,
            "min_project_deep_tasks": MIN_PROJECT_DEEP_TASKS,
            "near_duplicate_threshold": NEAR_DUPLICATE_THRESHOLD,
            "excluded_source_parts": sorted(EXCLUDED_SOURCE_PARTS),
            "interpretation": "Rejection pre-screen only; accepted structure never substitutes for behavioral admission or human review.",
        },
        "depth_diversity_satisfied": depth_diversity_satisfied,
        "suite_admission_eligible": (
            depth_diversity_satisfied
            and admission_evidence_verified
            and catalog.get("review_status") == "admitted"
        ),
        "admission_evidence_verified": admission_evidence_verified,
        "official_release_eligible": False,
        "blockers": blockers,
        "reason": (
            "All twelve legacy thin fixtures are explicitly rejected from the curated suite. "
            "They remain available as development controls; new deep application tasks and "
            "independent admission evidence are required."
        ),
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        help="also write the deterministic audit JSON to this repository-relative path",
    )
    args = parser.parse_args()
    rendered = json.dumps(audit(), indent=2, sort_keys=True) + "\n"
    print(rendered, end="")
    if args.output:
        output = args.output if args.output.is_absolute() else ROOT / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
