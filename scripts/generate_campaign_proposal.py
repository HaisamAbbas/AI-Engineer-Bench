"""ENG-021: Generate the official campaign proposal.

Produces a frozen campaign proposal document that specifies the 12 task x 3
entrant x 5 repetition matrix (180 trials), frozen profiles, cost reservation,
and assumption-based sample sizing. Does NOT use deterministic fixture results
for variance estimation (ENG-012 is BLOCKED).

Usage: python scripts/generate_campaign_proposal.py [--output <path>]
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TASKS = [
    {"id": "rag.document-freshness", "family_id": "knowledge-service-a", "category": "rag"},
    {"id": "rag.metadata-filter-topk", "family_id": "knowledge-service-b", "category": "rag"},
    {"id": "rag.citation-current-span", "family_id": "knowledge-service-c", "category": "rag"},
    {"id": "rag.embedding-version", "family_id": "knowledge-service-d", "category": "rag"},
    {"id": "ext.missingness", "family_id": "extraction-service-a", "category": "extraction"},
    {"id": "ext.batch-alignment", "family_id": "extraction-service-c", "category": "extraction"},
    {"id": "ext.unit-normalization", "family_id": "extraction-service-b", "category": "extraction"},
    {"id": "ext.partial-batch", "family_id": "extraction-service-d", "category": "extraction"},
    {"id": "tool.false-completion", "family_id": "assistant-service-e", "category": "tool_application"},
    {"id": "tool.idempotent-write", "family_id": "assistant-service-f", "category": "tool_application"},
    {"id": "tool.session-isolation", "family_id": "assistant-service-g", "category": "tool_application"},
    {"id": "tool.corrected-arguments", "family_id": "assistant-service-h", "category": "tool_application"},
]

ENTRYANTS = [
    {"id": "reference-agent", "track": "agents", "description": "Fixed reference coding-agent implementation"},
    {"id": "model-a", "track": "models", "description": "Model under test with reference prompt/tools"},
    {"id": "model-b", "track": "models", "description": "Second model under test"},
]

BASE_APPLICATION_TYPES = ["knowledge-service", "extraction-service", "assistant-service"]

BUDGET_PROFILE = {
    "schema_version": "aieb.budget/v1",
    "id": "cpu-standard-v1",
    "engineer_wall_seconds": 1200,
    "verification_wall_seconds": 300,
    "engineer_cpu": 2,
    "engineer_memory_mb": 1024,
    "verification_cpu": 2,
    "verification_memory_mb": 1024,
}

PER_ROLE_BUDGET_USD = {"engineer": 0.15, "dev_application": 0.02, "verifier_application": 0.02, "verifier_judge": 0.01}
ENVIRONMENT_UPPER_BOUND_USD = "0.10"

PROTOCOL = {"schema_version": "aieb.protocol/v1", "id": "official-protocol-v1", "scoring_digest": "9" * 64, "max_replacements": 2, "required_trace_coverage": False, "hard_cost_ranking": False}
MINIMUM_MEANINGFUL_EFFECT = 0.10


def wilson_interval(passes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total == 0:
        return (0.0, 0.0)
    n = total
    p = passes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, center - spread), min(1.0, center + spread))


def detectable_difference(n: int) -> float:
    """Approximate MDD for two-proportion z-test. ASSUMPTION-BASED, not pilot-derived."""
    z_alpha = 1.96
    z_beta = 0.84
    return 2 * (z_alpha + z_beta) * math.sqrt(0.25 / n) if n > 0 else float("inf")


def generate_sample_size_table() -> list[dict]:
    results = []
    for assumed_pass_rate in [0.1, 0.3, 0.5, 0.7, 0.9]:
        for repetitions in [3, 5, 7, 10, 15]:
            n = repetitions
            passes = int(assumed_pass_rate * n)
            lower, upper = wilson_interval(passes, n)
            mdd = detectable_difference(n)
            results.append({
                "assumed_pass_rate": assumed_pass_rate,
                "repetitions": repetitions,
                "passes": passes,
                "wilson_lower": round(lower, 4),
                "wilson_upper": round(upper, 4),
                "interval_width": round(upper - lower, 4),
                "detectable_difference": round(mdd, 4),
                "mdd_below_mme": mdd < MINIMUM_MEANINGFUL_EFFECT,
            })
    return results


def compute_trial_count() -> dict:
    tasks = len(TASKS)
    entrants = len(ENTRYANTS)
    repetitions = 5
    dependency_modes = 1
    total_trials = tasks * entrants * repetitions * dependency_modes
    return {
        "tasks": tasks, "entrants": entrants, "repetitions": repetitions,
        "dependency_modes": dependency_modes,
        "total_trials": total_trials,
        "max_replacements": PROTOCOL["max_replacements"],
        "total_with_replacements": total_trials * (1 + PROTOCOL["max_replacements"]),
    }


def compute_cost_reservation() -> dict:
    trial_count = compute_trial_count()
    total_planned = trial_count["total_with_replacements"]
    role_total_per_trial = sum(float(PER_ROLE_BUDGET_USD[r]) for r in PER_ROLE_BUDGET_USD)
    total_role_cost = role_total_per_trial * total_planned
    total_env_cost = float(ENVIRONMENT_UPPER_BOUND_USD) * total_planned
    grand_total = total_role_cost + total_env_cost
    return {
        "enforcement": "estimated_time_limited",
        "reservation_basis": "assumption-based estimates; no real provider pricing exists",
        "planned_trials_including_replacements": total_planned,
        "per_role_budget_usd": PER_ROLE_BUDGET_USD,
        "environment_upper_bound_usd": ENVIRONMENT_UPPER_BOUND_USD,
        "total_engineer_cost": round(float(PER_ROLE_BUDGET_USD["engineer"]) * total_planned, 2),
        "total_dev_application_cost": round(float(PER_ROLE_BUDGET_USD["dev_application"]) * total_planned, 2),
        "total_verifier_application_cost": round(float(PER_ROLE_BUDGET_USD["verifier_application"]) * total_planned, 2),
        "total_verifier_judge_cost": round(float(PER_ROLE_BUDGET_USD["verifier_judge"]) * total_planned, 2),
        "total_role_cost": round(total_role_cost, 2),
        "total_environment_cost": round(total_env_cost, 2),
        "grand_total_reservation_usd": round(grand_total, 2),
        "grand_total_with_reserve_usd": round(grand_total * 1.20, 2),
        "hard_cap_status": "NOT ENFORCED - no provider reservation integration exists; estimates only",
    }


def generate_cells() -> list[dict]:
    trial_count = compute_trial_count()
    cells = []
    for task in TASKS:
        for entrant in ENTRYANTS:
            for mode in ["fixture"]:
                for rep in range(1, trial_count["repetitions"] + 1):
                    cells.append({
                        "cell_id": f"{task['id']}::{entrant['id']}::{mode}::r{rep}",
                        "task_id": task["id"], "family_id": task["family_id"], "category": task["category"],
                        "entrant_id": entrant["id"], "track": entrant["track"],
                        "repetition": rep, "dependency_mode": mode, "budget_profile_id": BUDGET_PROFILE["id"],
                    })
    return cells


def generate_proposal() -> dict:
    sample_size_table = generate_sample_size_table()
    trial_count = compute_trial_count()
    cost_reservation = compute_cost_reservation()

    infra_invalid_policy = {
        "retention": {
            "valid_failures": "retained as scored FAIL (terminal_status='fail')",
            "infrastructure_invalid": "retained for attribution but NOT scored; replaced per max_replacements policy",
            "exclusions": "invalid attempts reported separately with attribution, never dropped from cost totals",
        },
        "cost_accounting": "total_campaign_cost_usd includes all attempts; cost_per_resolution only counts scored (valid AND verdict IS NOT NULL). ENG-011 logic.",
        "citation": "services/api/src/aieb_api/aggregation.py::summarize(); tests/test_analysis.py ST-01/ST-02/ST-03",
    }

    proposal = {
        "schema_version": "aieb.campaign-proposal/v1",
        "label": "official-public-origin",
        "contamination_clause": (
            "This campaign proposal uses the twelve public development tasks in suites/dev/. "
            "Per spec section 22, private examples on public tasks do not constitute an "
            "uncontaminated hidden benchmark. A genuine held-out family requires distinct "
            "application packages that do not exist yet. Labeled official-public-origin, not held-out."
        ),
        "status": "ready-for-review",
        "release_status": "NOT OFFICIALLY RELEASED - all gates BLOCKED pending authorization",
        "blocks": [
            {"gate": "ENG-001", "reason": "Real installed agent smoke blocked on provider/model authorization, credentials, existing cap"},
            {"gate": "ENG-012", "reason": "Real pilot execution blocked on provider/model/cap authorization"},
            {"gate": "ENG-019", "reason": "Hardened isolation (VM provider) deferred; Harbor Docker is not VM-equivalent"},
            {"gate": "ENG-020", "reason": "Staging/production deployment blocked on cloud authorization"},
            {"gate": "independent-review", "reason": "All 12 tasks pending independent admission review; ENG-021 not COMPLETE"},
            {"gate": "python-lint-type-ci", "reason": "Python lint/type-checking CI gate not implemented (400+ pre-existing findings)"},
        ],
        "campaign_specification": {
            "suite_release": "development-preview-proposed",
            "track": "agents",
            "dependency_mode": "fixture",
            "budget_profile_id": BUDGET_PROFILE["id"],
            "protocol_id": PROTOCOL["id"],
            "profile_compatibility": ["cpu-fixture-standard-v1"],
            "tasks": [t["id"] for t in TASKS],
            "entrants": [e["id"] for e in ENTRYANTS],
            "repetitions": trial_count["repetitions"],
            "total_trials": trial_count["total_trials"],
            "total_trials_including_replacements": trial_count["total_with_replacements"],
        },
        "trial_matrix": {
            "description": f"{trial_count['tasks']} tasks x {trial_count['entrants']} entrants x {trial_count['repetitions']} reps x {trial_count['dependency_modes']} mode = {trial_count['total_trials']} trials",
            "interleaving_ordering": "cyclic permutation: for each repetition, cycle entrants across tasks (spec section 19)",
            "cells": generate_cells(),
        },
        "infrastructure_invalid_policy": infra_invalid_policy,
        "minimum_meaningful_effect": {
            "value": MINIMUM_MEANINGFUL_EFFECT,
            "unit": "decimal (10 percentage points)",
            "basis": "Prespecified before any official run; not derived from data (spec section 28)",
        },
        "sample_size_methodology": {
            "note_critical": (
                "REAL PILOT VARIANCE UNAVAILABLE. ENG-012 is BLOCKED; no real pilot has been executed. "
                "The deterministic fixture results (ENG-005/010) are deterministic, so observed variance "
                "is ~zero. Using them for sample-size projection would produce fabricated statistical "
                "basis (the exact failure mode ENG-011 audits against). This table is ASSUMPTION-BASED: "
                "Wilson intervals and detectable differences across assumed pass rates at various repetition counts."
            ),
            "assumptions": [
                "True pass rate p is unknown and assumed in {0.1, 0.3, 0.5, 0.7, 0.9}",
                "Binomial observation model per trial cell (pass/fail per requirement)",
                "Independent observations within a cell (repetitions are independent trials)",
                "Wilson score interval at z=1.96 (95% confidence)",
                "MDD from two-proportion z-test at alpha=0.05, power=0.80",
            ],
            "sensitivity_table": sample_size_table,
            "conclusion": (
                "At 5 repetitions (the proposed count), the minimum detectable difference is ~0.62 "
                "regardless of assumed pass rate - well above the prespecified 0.10 MME. The campaign "
                "is NOT statistically powered to detect the MME at 5 repetitions. Real pilot variance "
                "is required to justify a larger repetition count. This proposal freezes the 5-repetition "
                "plan as a starting point that MUST be revised after ENG-012 authorization and pilot measurement."
            ),
        },
        "statistical_protocol": {
            "per_task": "p_hat = s/n (Wilson interval)",
            "suite_rate": "Unweighted mean across the frozen task list (spec section 28)",
            "clustering": {
                "base_application_types": BASE_APPLICATION_TYPES,
                "actual_count": len(BASE_APPLICATION_TYPES),
                "caveat": "Spec section 28 acknowledges six projects; this repo has 3 base application types across 12 family IDs (4 per type). Intervals are exploratory, not population-wide.",
            },
            "pass_k_estimator": "For pass^k at n>=k: choose(s,k)/choose(n,k), not pass@k",
            "no_p95_claims": "No p95 claims from small n (<100)",
        },
        "cost_reservation": cost_reservation,
        "analysis_plan": {
            "aggregation_function": "services/api/src/aieb_api/aggregation.py::aggregate_campaign_snapshot",
            "planned_cells_wired": True,
            "retention_basis": "ENG-011: valid failures retained as scored FAIL; infrastructure-invalid attempts retained for attribution, excluded from cost_per_resolution but included in total_campaign_cost_usd",
            "limitations": [
                "Per-entrant valid-vs-planned coverage count is a disclosed non-field",
                "Cluster intervals are exploratory (3 base types, not 6 projects)",
                "cost_per_resolution only counts scored observations (valid AND verdict IS NOT NULL)",
                "total_campaign_cost_usd includes all attempts (valid + invalid)",
            ],
        },
        "freeze_declarations": {
            "tasks": [t["id"] for t in TASKS],
            "entrants": [e["id"] for e in ENTRYANTS],
            "repetitions": trial_count["repetitions"],
            "dependency_mode": "fixture",
            "budget_profile_id": BUDGET_PROFILE["id"],
            "protocol_id": PROTOCOL["id"],
            "max_replacements": PROTOCOL["max_replacements"],
            "ordering_rule": "interleaved cyclic permutation",
            "infrastructure_invalid_policy": "retained, not scored; total cost includes invalid attempts",
            "minimum_meaningful_effect": MINIMUM_MEANINGFUL_EFFECT,
        },
        "review_status": {
            "task_admission_review": "PENDING for all 12 tasks",
            "evaluator_review": "PENDING - automated evidence report prepared by scripts/evaluator_review.py",
            "provenance_contamination_review": "PENDING - automated overlap check prepared by scripts/provenance_overlap_review.py",
            "independent_human_approval": "REQUIRED before ENG-022; not substituted by this preparation",
        },
    }
    return proposal


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Output JSON path (recommended: docs/implementation/evidence/ENG-021/)")
    args = parser.parse_args()

    proposal = generate_proposal()
    output = json.dumps(proposal, sort_keys=True, ensure_ascii=False, indent=2) + "\n"

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
        print(f"Wrote campaign proposal to {args.output}")
    print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())