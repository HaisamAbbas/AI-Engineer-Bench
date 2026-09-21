"""Static and optional executable admission checks for one development task.

This command produces evidence, never an independent-human approval or an
official publication.  `--execute` delegates the baseline/reference/control
matrix to the task's existing maintainer admission module.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REQUIRED_FILES = ("task.yaml", "instruction.md", "provenance.json", "contracts/application-api.md")
REQUIRED_DIRS = ("repo", "reference", "alternative", "counterexamples", "dev_tests", "environment")


class AdmissionError(ValueError):
    pass


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def inspect_task(task: Path) -> dict[str, object]:
    task = task.resolve()
    missing = [name for name in REQUIRED_FILES if not (task / name).is_file()]
    missing += [name for name in REQUIRED_DIRS if not (task / name).is_dir()]
    if missing:
        raise AdmissionError("task package is incomplete: " + ", ".join(missing))
    try:
        manifest = yaml.safe_load((task / "task.yaml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise AdmissionError(f"cannot read task.yaml: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") not in {"aieb.task/v1", "aieb.task/v2"}:
        raise AdmissionError("task.yaml must declare a supported versioned AIEB schema")
    evaluator = manifest.get("evaluator")
    source = manifest.get("source")
    if not isinstance(evaluator, dict) or not evaluator.get("evaluator_digest") or not evaluator.get("official_fixture_ref"):
        raise AdmissionError("task must declare a real evaluator digest and private fixture reference")
    if not isinstance(source, dict) or not source.get("license"):
        raise AdmissionError("task must declare license metadata")
    provenance = json.loads((task / "provenance.json").read_text(encoding="utf-8"))
    if not isinstance(provenance, dict) or provenance.get("contains_customer_data") is True:
        raise AdmissionError("customer/private source data is not admissible")
    leaked: list[str] = []
    for path in task.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        rel = path.relative_to(task).as_posix().lower()
        if any(token in rel for token in ("maintainer", "holdout", "private", "answer-key")):
            leaked.append(rel)
    if leaked:
        raise AdmissionError("private evaluator material is inside the public task package: " + ", ".join(leaked))
    if not _inside(task, task / "repo"):
        raise AdmissionError("starter repository escapes task package")
    return {
        "task_id": manifest.get("id"),
        "task_version": manifest.get("version"),
        "development_only": True,
        "independent_review": "pending",
        "automated_checks": {
            "package_shape": "pass",
            "license_provenance": "pass",
            "private_fixture_separation": "pass",
            "variant_controls_present": all(any((task / directory).iterdir()) for directory in ("reference", "alternative", "counterexamples")),
        },
    }


def execute_matrix(task: Path, info: dict[str, object]) -> dict[str, object]:
    runtime = json.loads((task / "runtime.json").read_text(encoding="utf-8")) if (task / "runtime.json").exists() else None
    if not isinstance(runtime, dict):
        return {"status": "not-run", "reason": "no runtime.json admission entry"}
    module = importlib.import_module(str(runtime["admission_module"]))
    result = getattr(module, str(runtime["admission_function"]))()
    info["execution"] = {"status": "evidence-collected", "result": result}
    return info


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", type=Path)
    parser.add_argument("--execute", action="store_true", help="run the task's maintainer matrix; never publishes")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        info = inspect_task(args.task)
        if args.execute:
            info = execute_matrix(args.task.resolve(), info)
        output = json.dumps(info, indent=2, sort_keys=True) + "\n"
        if args.output:
            target = args.output.resolve()
            if not _inside(ROOT, target):
                raise AdmissionError("--output must stay inside the repository")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(output, encoding="utf-8")
        print(output, end="")
        return 0
    except (AdmissionError, KeyError, ImportError) as exc:
        print(json.dumps({"status": "rejected", "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
