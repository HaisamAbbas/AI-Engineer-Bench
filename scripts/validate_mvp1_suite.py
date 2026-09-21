"""Audit the development catalog against the v2 MVP-1 depth requirements.

This is a fail-closed audit, not an admission or release command. It identifies
shallow/blocked tasks instead of declaring synthetic variants to be genuine
applications.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "suites" / "dev" / "catalog.json"

REQUIRED_BY_CATEGORY = {
    # A suite is deep when its tasks collectively cover the family. Requiring
    # every capability on every task incorrectly labels focused, complementary
    # tasks as shallow. Each task must declare and demonstrate its own focus.
    "rag": ("index", "retriev"),
    "extraction": ("schema",),
    "tool_app": ("state",),
}


def audit() -> dict[str, object]:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    rows: list[dict[str, object]] = []
    for entry in catalog.get("tasks", []):
        task = ROOT / "suites" / "dev" / entry["path"]
        task_yaml = (task / "task.yaml").read_text(encoding="utf-8").lower()
        contract = (task / "contracts" / "application-api.md").read_text(encoding="utf-8").lower()
        metadata_path = task / "mvp1.yaml"
        metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
        if not isinstance(metadata, dict):
            metadata = {}
        text = task_yaml + "\n" + contract + "\n" + yaml.safe_dump(metadata).lower() + "\n" + entry["id"].lower()
        category = re.search(r"^category:\s*([^\s]+)", task_yaml, re.MULTILINE)
        category_name = category.group(1) if category else "unknown"
        required = REQUIRED_BY_CATEGORY.get(category_name, ())
        missing_depth = [term for term in required if term not in text]
        covered = metadata.get("covered_capabilities", [])
        if not isinstance(covered, list) or len(covered) < 2:
            missing_depth.append("at-least-two-covered-capabilities")
        elif any(str(cap).lower() not in text for cap in covered):
            missing_depth.append("declared-capability-not-in-contract")
        missing_package = [name for name in ("instruction.md", "reference", "alternative", "counterexamples", "dev_tests", "environment") if not (task / name).exists()]
        required_metadata = ("ticket", "application_surface", "hidden_cases", "regression_requirements", "operational_edge_case", "shortcut_controls", "reference", "baseline", "alternative")
        missing_metadata = [name for name in required_metadata if not metadata.get(name)]
        rows.append({
            "id": entry["id"],
            "family_id": entry.get("family_id"),
            "category": category_name,
            "status": "candidate" if not missing_depth and not missing_package and not missing_metadata else "shallow-or-blocked",
            "missing_depth_indicators": missing_depth,
            "missing_package_parts": missing_package,
            "missing_mvp1_metadata": missing_metadata,
            "independent_review": "pending",
            "official": False,
        })
    categories = {row["category"] for row in rows}
    return {
        "schema_version": "aieb.mvp1-suite-audit/v1",
        "catalog_review_status": catalog.get("review_status", "unknown"),
        "category_count": len(categories),
        "required_categories": sorted(REQUIRED_BY_CATEGORY),
        "category_diversity_satisfied": categories == set(REQUIRED_BY_CATEGORY),
        "tasks": rows,
        "official_release_eligible": False,
        "reason": "Development-only audit; independent review, genuine depth, holdout and official gates remain pending.",
    }


if __name__ == "__main__":
    print(json.dumps(audit(), indent=2, sort_keys=True))
