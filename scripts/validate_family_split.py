"""ENG-021: Validate family splits and clustering for the official campaign proposal.

Per spec section 23 ("Related-work audit") and section 28 ("cluster intervals"):
- Tasks are grouped by family_id, which maps to a base application type.
- With 3 base application types (knowledge-service, extraction-service,
  assistant-service) across 12 family IDs, cluster intervals are exploratory,
  not population-wide.
- This script reports the ACTUAL clustering as found in the catalog, not
  the architecture doc's "six projects" aspiration.

Does NOT validate against a number the repo doesn't have. Reports what exists.

Usage: python scripts/validate_family_split.py [--output <path>]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_catalog() -> dict:
    return json.loads((ROOT / "suites" / "dev" / "catalog.json").read_text(encoding="utf-8"))


def _load_task_yaml(task_id: str) -> dict:
    """Load a task's task.yaml to inspect its declared structure."""
    import yaml
    catalog = _load_catalog()
    task_entry = next((t for t in catalog["tasks"] if t["id"] == task_id), None)
    if task_entry is None:
        raise ValueError(f"unknown task: {task_id}")
    task_yaml_path = ROOT / "suites" / "dev" / task_entry["path"] / "task.yaml"
    if not task_yaml_path.exists():
        return {}
    return yaml.safe_load(task_yaml_path.read_text(encoding="utf-8"))


def validate_family_split() -> dict:
    catalog = _load_catalog()
    tasks = catalog["tasks"]

    # Group by family_id
    families: dict[str, list[str]] = {}
    for task in tasks:
        family = task["family_id"]
        families.setdefault(family, []).append(task["id"])

    # Derive base application types from family_id prefixes
    # family_ids are like "knowledge-service-a", "extraction-service-c", "assistant-service-e"
    base_types: dict[str, list[str]] = {}
    for family, task_ids in families.items():
        # Split on the last hyphen+letter suffix
        parts = family.rsplit("-", 1)
        if len(parts) == 2 and len(parts[1]) == 1 and parts[1].isalpha():
            base = parts[0]
        else:
            base = family
        base_types.setdefault(base, []).append(family)

    # Read each task's task.yaml for additional metadata
    task_details = []
    for task in tasks:
        task_yaml = _load_task_yaml(task["id"])
        task_details.append({
            "id": task["id"],
            "path": task["path"],
            "family_id": task["family_id"],
            "category": task_yaml.get("category", "unknown"),
            "activity": task_yaml.get("activity", "unknown"),
            "requirements_count": len(task_yaml.get("requirements", [])),
            "review_status": task_yaml.get("evaluator", {}).get("official_fixture_ref", "unknown"),
        })

    # Check: is each family's backend genuinely different?
    # Per ENG-013 STATUS: "each task's backend.py does encode a genuinely different
    # bug/domain (not renamed copies), though several are very thin (2-4 lines
    # of logic) inside a shared, intentionally generic HTTP-server harness"
    # We can't verify this from here - that's the evaluator review's job.
    # We report the structural facts.

    clustering = {
        "base_application_types": sorted(base_types.keys()),
        "base_type_count": len(base_types),
        "families_per_base_type": {k: len(v) for k, v in sorted(base_types.items())},
        "family_to_task_count": {k: len(v) for k, v in sorted(families.items())},
        "arch_section_28_acknowledged_projects": 6,
        "actual_project_count": len(base_types),
        "caveat": (
            "Spec section 28 acknowledges six projects for cluster-interval validity. "
            f"This repository's dev catalog maps {len(base_types)} base application types "
            f"across 12 family IDs (4 per type). With 3 < 6 base types, cluster intervals "
            "are exploratory, not population-wide. This is stated explicitly per the "
            "review guidance: do not validate against a number the repo doesn't have."
        ),
        "diversity_statement": (
            "The twelve family IDs ARE distinct (rag.metadata-filter-topk != rag.citation-current-span, "
            "etc.), each encoding a genuinely different bug/domain per ENG-013's admission evidence. "
            "However, several backends are 'very thin (2-4 lines of logic) inside a shared, "
            "intentionally generic HTTP-server harness' (ENG-013 STATUS). The distinction is real "
            "but shallow - documented as-is rather than papered over."
        ),
    }

    return {
        "schema_version": "aieb.family-split/v1",
        "label": "official-public-origin",
        "clustering": clustering,
        "task_details": task_details,
        "family_groups": families,
        "review_note": "This is a structural analysis of the catalog, not an independent diversity review. Independent admission review remains PENDING.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Output JSON path")
    args = parser.parse_args()

    result = validate_family_split()
    output = json.dumps(result, sort_keys=True, ensure_ascii=False, indent=2) + "\n"

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
        print(f"Wrote family split validation to {args.output}", file=sys.stderr)
    print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
