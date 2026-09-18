"""ENG-021: Generate official-public-origin holdout fixtures for a development task.

Produces private, non-publicly-committed fixtures that exercise the same
requirement set as the public dev_data/ but with distinct input values,
so the evaluator can test a candidate against inputs not derivable from the
public package. Generates into an OUTSIDE-THE-REPO directory only - writes
are refused anywhere under the repository root (mirroring ENG-019's mount
check discipline: enforce the boundary, don't document it).

Label: official-public-origin. NOT contamination-free held-out per spec
section 22 ("Twelve public tasks with private examples do not become an
uncontaminated hidden benchmark"). A genuine held-out family means
distinct application packages that do not exist yet.

Requires:
  --task <task-id>           One of the 12 catalogued task IDs
  --output-dir <path>        Destination directory OUTSIDE the repo root
  --seed <int>               Seed for the deterministic fixture generator
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TASK_FIXTURE_GENERATORS = {
    "rag.document-freshness": "_rag01_holdout",
    "rag.metadata-filter-topk": "_rag02_holdout",
    "rag.citation-current-span": "_rag03_holdout",
    "rag.embedding-version": "_rag04_holdout",
    "ext.missingness": "_ext01_holdout",
    "ext.batch-alignment": "_ext02_holdout",
    "ext.unit-normalization": "_ext03_holdout",
    "ext.partial-batch": "_ext04_holdout",
    "tool.false-completion": "_tool01_holdout",
    "tool.idempotent-write": "_tool02_holdout",
    "tool.session-isolation": "_tool03_holdout",
    "tool.corrected-arguments": "_tool04_holdout",
}


def _rag01_holdout(seed: int) -> tuple[dict, list[dict]]:
    rng = random.Random(seed)
    tokens = ["amber", "bravo", "cedar", "delta", "echo"]
    rng.shuffle(tokens)
    a, b, c, d, e = tokens[:5]
    documents = {
        a: {"version": 1, "text": f"{a} original archive", "metadata": {"group": "updates"}},
        b: {"version": 1, "text": f"{b} removable archive", "metadata": {"group": "deletes"}},
        c: {"version": 1, "text": f"{c} unaffected archive", "metadata": {"group": "control"}},
    }
    cases: list[dict] = []
    cases.append({"case_id": "latest-version-visible", "action": "put", "doc_id": a, "version": 2, "text": f"{d} replacement fact", "metadata": documents[a]["metadata"]})
    cases.append({"case_id": "idempotent-event-replay", "action": "put", "doc_id": a, "version": 2, "text": f"{d} replacement fact", "metadata": documents[a]["metadata"]})
    cases.append({"case_id": "equal-version-conflict", "action": "put", "doc_id": a, "version": 2, "text": f"{e} conflicting content", "metadata": documents[a]["metadata"], "expect_status": 409})
    cases.append({"case_id": "lower-version-rejected", "action": "put", "doc_id": a, "version": 1, "text": f"{a} stale overwrite attempt", "metadata": documents[a]["metadata"]})
    cases.append({"case_id": "deleted-content-absent", "action": "delete", "doc_id": b, "version": 2})
    cases.append({"case_id": "stale-resurrection-blocked", "action": "put", "doc_id": b, "version": 1, "text": f"{b} stale resurrection attempt", "metadata": documents[b]["metadata"]})
    cases.append({"case_id": "higher-version-recreation", "action": "put", "doc_id": b, "version": 3, "text": f"{d} recreation fact", "metadata": documents[b]["metadata"]})
    cases.append({"case_id": "unaffected-documents-preserved", "query": c})
    cases.append({"case_id": "citation-version-mapping", "query": d, "expect_chunk_id_prefix": f"{a}:2:"})
    fixture = {
        "fixture_version": f"holdout-rag01-v1-seed{seed}",
        "seed": seed,
        "documents": documents,
        "replacement_text": f"{d} replacement fact",
        "recreation_text": f"{d} recreation fact",
        "heldout_cases": cases,
    }
    return fixture, cases


def _rag02_holdout(seed: int) -> tuple[dict, list[dict]]:
    rng = random.Random(seed)
    docs = [{"doc_id": f"holdout-doc-{i}", "version": 1, "text": f"{rng.choice(['archive','record','document'])} {i}", "metadata": {"group": rng.choice(["alpha", "beta", "gamma", "delta"])}} for i in range(12)]
    cases = [
        {"case_id": "filter-suppresses-wrong-group", "filter": {"group": "alpha"}, "expect_only_groups": ["alpha"]},
        {"case_id": "filter-combines-with-topk", "filter": {"group": "beta"}, "top_k": 3, "expect_at_most": 3},
    ]
    return {"fixture_version": f"holdout-rag02-v1-seed{seed}", "seed": seed, "documents": docs, "heldout_cases": cases}, cases


def _rag03_holdout(seed: int) -> tuple[dict, list[dict]]:
    topic = random.Random(seed).choice(["climate", "finance", "health"])
    doc_text = f"holdout passage about {topic} at index {seed}"
    chunks = [f"{doc_text} section {i}" for i in range(4)]
    cases = [{"case_id": "citation-points-to-correct-chunk", "query": doc_text, "expect_chunk_index_in_cited_doc": True}]
    return {"fixture_version": f"holdout-rag03-v1-seed{seed}", "seed": seed, "document": {"id": f"holdout-cite-{seed}", "version": 1, "chunks": chunks}, "heldout_cases": cases}, cases


def _rag04_holdout(seed: int) -> tuple[dict, list[dict]]:
    cases = [{"case_id": "model-switch-does-not-break-search", "expect_results_within": 3}]
    return {"fixture_version": f"holdout-rag04-v1-seed{seed}", "seed": seed, "documents": [{"id": f"holdout-emb-{seed}", "version": 1, "text": f"embedding holdout {seed}"}], "heldout_cases": cases}, cases


def _ext01_holdout(seed: int) -> tuple[dict, list[dict]]:
    rng = random.Random(seed)
    doc = {f"field-{i}": (None if rng.random() < 0.3 else f"holdout-value-{seed}-{i}") for i in range(6)}
    cases = [{"case_id": "missing-fields-not-invented", "expect_missing_present_as_missing": True}]
    return {"fixture_version": f"holdout-ext01-v1-seed{seed}", "seed": seed, "document": doc, "heldout_cases": cases}, cases


def _ext02_holdout(seed: int) -> tuple[dict, list[dict]]:
    rng = random.Random(seed)
    docs = [{"id": f"holdout-batch-{i}", "text": f"batch content {i} seed {seed}", "metadata": {"order": i}} for i in range(8)]
    rng.shuffle(docs)
    cases = [{"case_id": "outputs-attached-to-correct-documents", "permuted": True}]
    return {"fixture_version": f"holdout-ext02-v1-seed{seed}", "seed": seed, "documents": docs, "heldout_cases": cases}, cases


def _ext03_holdout(seed: int) -> tuple[dict, list[dict]]:
    cases = [{"case_id": "unit-normalization-preserves-values", "expect_normalized": "dollars"}]
    return {"fixture_version": f"holdout-ext03-v1-seed{seed}", "seed": seed, "documents": [{"value": str(seed * 100), "unit": "cents"}, {"value": str(seed), "unit": "dollars"}], "heldout_cases": cases}, cases


def _ext04_holdout(seed: int) -> tuple[dict, list[dict]]:
    cases = [{"case_id": "one-malformed-result-does-not-drop-successful-items", "expect_all_valid_processed": True}]
    return {"fixture_version": f"holdout-ext04-v1-seed{seed}", "seed": seed, "documents": [{"id": f"partial-{i}", "valid": i != 2} for i in range(5)], "heldout_cases": cases}, cases


def _tool01_holdout(seed: int) -> tuple[dict, list[dict]]:
    cases = [{"case_id": "tool-failure-reported-not-completion", "expect_ledger_records_only_actual_calls": True}]
    return {"fixture_version": f"holdout-tool01-v1-seed{seed}", "seed": seed, "heldout_cases": cases}, cases


def _tool02_holdout(seed: int) -> tuple[dict, list[dict]]:
    cases = [{"case_id": "retrying-ambiguous-write-does-not-duplicate", "expect_exactly_one_effect": True}]
    return {"fixture_version": f"holdout-tool02-v1-seed{seed}", "seed": seed, "heldout_cases": cases}, cases


def _tool03_holdout(seed: int) -> tuple[dict, list[dict]]:
    cases = [{"case_id": "session-state-does-not-leak", "expect_interleaved_sessions_isolated": True}]
    return {"fixture_version": f"holdout-tool03-v1-seed{seed}", "seed": seed, "heldout_cases": cases}, cases


def _tool04_holdout(seed: int) -> tuple[dict, list[dict]]:
    cases = [{"case_id": "application-uses-corrected-arguments", "expect_final_state_matches_correction": True}]
    return {"fixture_version": f"holdout-tool04-v1-seed{seed}", "seed": seed, "heldout_cases": cases}, cases


def _generate_fixture(task_id: str, seed: int) -> tuple[dict, list[dict]]:
    generator_name = TASK_FIXTURE_GENERATORS[task_id]
    generator = globals()[generator_name]
    return generator(seed)


def _validate_task_id(task_id: str) -> Path:
    catalog_path = ROOT / "suites" / "dev" / "catalog.json"
    if not catalog_path.exists():
        raise FileNotFoundError(f"catalog not found at {catalog_path}")
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    if catalog.get("schema_version") != "aieb.dev-catalog/v1":
        raise ValueError(f"unsupported catalog schema: {catalog.get('schema_version')}")
    tasks = catalog.get("tasks", [])
    task = next((t for t in tasks if t["id"] == task_id), None)
    if task is None:
        raise ValueError(f"unknown task id: {task_id}. Available: {[t['id'] for t in tasks]}")
    task_dir = ROOT / "suites" / "dev" / task["path"]
    if not task_dir.exists():
        raise FileNotFoundError(f"task directory not found: {task_dir}")
    return task_dir


def _validate_output_dir(output_dir: Path) -> Path:
    """Refuse to write anywhere under the repository root."""
    root = ROOT.resolve()
    resolved = output_dir.resolve()
    if resolved == root or root in resolved.parents:
        raise FileExistsError(
            f"refusing to write holdout fixture under the repository root ({resolved}); "
            f"holdout fixtures must be placed outside the public repository"
        )
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _compute_fixture_digest(fixture: dict) -> str:
    serialized = json.dumps(fixture, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _write_holdout_bundle(task_id: str, task_dir: Path, output_dir: Path, seed: int) -> dict:
    """Write the holdout fixture bundle (generator + data + provenance) to the output dir."""
    import yaml
    task_yaml = yaml.safe_load((task_dir / "task.yaml").read_text(encoding="utf-8"))
    requirement_ids = [r["id"] for r in task_yaml.get("requirements", [])]

    fixture, cases = _generate_fixture(task_id, seed)
    fixture_digest = _compute_fixture_digest(fixture)
    case_ids = {c["case_id"] for c in cases}
    coverage = {
        "requirements_total": len(requirement_ids),
        "cases_total": len(cases),
        "requirements_with_direct_cases": len(set(requirement_ids) & case_ids),
        "requirements_without_direct_cases": sorted(set(requirement_ids) - case_ids),
        "note": "Not every requirement has a 1:1 case_id; some cases cover multiple requirements and some requirements are structural (api-ready) rather than data-driven test cases.",
    }

    # Write portable generator (self-contained, no aieb dependency)
    generator_source = f'''"""Portable holdout fixture generator for {task_id}.
Label: official-public-origin (NOT contamination-free held-out per spec section 22).
Generated by scripts/curate_holdout.py with seed={seed}.
"""
from __future__ import annotations
import json

_FIXTURE = {json.dumps(fixture, sort_keys=True, ensure_ascii=False, indent=2)}


def generate(seed: int = {seed}) -> dict:
    """Return the holdout fixture data for this task."""
    data = json.loads(json.dumps(_FIXTURE))  # deep copy
    data["seed"] = seed
    return data
'''
    (output_dir / "generate.py").write_text(generator_source, encoding="utf-8")

    # Write fixture data as JSON
    (output_dir / "fixture.json").write_text(json.dumps(fixture, sort_keys=True, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Write provenance (references fixture by digest, NOT the cases themselves —
    # a leaked provenance file must not become a leaked answer key)
    provenance = {
        "schema_version": "aieb.holdout-provenance/v1",
        "label": "official-public-origin",
        "contamination_clause": (
            "This fixture exercises the same requirements as the public development task "
            "but with distinct input values. Per spec section 22, private examples on public "
            "tasks do not constitute an uncontaminated hidden benchmark. A genuine held-out "
            "family requires distinct application packages that do not exist yet."
        ),
        "task_id": task_id,
        "task_path": f"suites/dev/{task_dir.name}",
        "generated_by": "scripts/curate_holdout.py",
        "seed": seed,
        "fixture_digest": fixture_digest,
        "fixture_file": "fixture.json",
        "requirement_coverage": coverage,
        "case_count": len(cases),
        "case_ids": [c["case_id"] for c in cases],
        "output_files": ["generate.py", "fixture.json", "provenance.json"],
        "redaction_note": "This provenance file does NOT carry case answers — the fixture.json file holds them and is referenced by the digest above. A leaked provenance file reveals coverage and case IDs but not expected outputs.",
    }
    (output_dir / "provenance.json").write_text(json.dumps(provenance, sort_keys=True, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return provenance


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--task", required=True, choices=list(TASK_FIXTURE_GENERATORS.keys()), help="Task ID to generate holdout fixture for")
    parser.add_argument("--output-dir", required=True, type=Path, help="Destination directory OUTSIDE the repository root")
    parser.add_argument("--seed", type=int, default=1337, help="Deterministic seed for fixture generation")
    args = parser.parse_args()

    task_dir = _validate_task_id(args.task)
    validated_output = _validate_output_dir(args.output_dir)

    provenance = _write_holdout_bundle(args.task, task_dir, validated_output, args.seed)
    print(json.dumps(provenance, sort_keys=True, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
