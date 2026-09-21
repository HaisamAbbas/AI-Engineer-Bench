"""Supported local AIEB commands for the deterministic RAG-01 vertical path."""

from __future__ import annotations

import argparse
import hashlib
import html
import importlib
import json
import os
import platform
import sys
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import yaml
from aieb_core.canonical import content_hash
from aieb_core.models import SubmissionPolicy, TaskRevision
from aieb_runner.artifacts import FilesystemArtifactStore
from aieb_runner.lifecycle import AttemptConfig, EngineeringCommand, LocalAttemptRunner
from pydantic import ValidationError

from . import operator as operator_cli

EXIT_INVALID = 2
EXIT_MISSING_CAPABILITY = 3
EXIT_INFRASTRUCTURE_INCOMPLETE = 4
EXIT_UNSOLVED = 5
STATE_SCHEMA = "aieb.local-state/v1"

# Local deterministic development fixtures only.  Each mapping names the candidate
# package and its maintainer-owned evaluator; there is no candidate-side evaluator import.
TASK_RUNTIMES = {
    "rag.document-freshness": ("knowledge_service", "tests.maintainer.rag01.evaluator", "scripts.run_rag01_admission", "evaluate_variant"),
    "rag.metadata-filter-topk": ("search_service", "tests.maintainer.rag02.evaluator", "scripts.run_rag02_admission", "one"),
    "rag.citation-current-span": ("citation_service", "tests.maintainer.rag03.evaluator", "scripts.run_rag03_admission", "one"),
    "rag.embedding-version": ("embedding_service", "tests.maintainer.rag04.evaluator", "scripts.run_rag04_admission", "one"),
    "ext.missingness": ("missingness_service", "tests.maintainer.ext01.evaluator", "scripts.run_ext01_admission", "run_matrix"),
    "ext.batch-alignment": ("extraction_service", "tests.maintainer.ext02.evaluator", "scripts.run_ext02_admission", "one"),
    "ext.unit-normalization": ("unit_service", "tests.maintainer.ext03.evaluator", "scripts.run_ext03_admission", "run_matrix"),
    "ext.partial-batch": ("batch_service", "tests.maintainer.ext04.evaluator", "scripts.run_ext04_admission", "run_matrix"),
    "tool.false-completion": ("workflow_service", "tests.maintainer.tool01.evaluator", "scripts.run_tool01_admission", "one"),
    "tool.idempotent-write": ("write_service", "tests.maintainer.tool02.evaluator", "scripts.run_tool02_admission", "run_matrix"),
    "tool.session-isolation": ("session_service", "tests.maintainer.tool03.evaluator", "scripts.run_tool03_admission", "run_matrix"),
    "tool.corrected-arguments": ("correction_service", "tests.maintainer.tool04.evaluator", "scripts.run_tool04_admission", "run_matrix"),
}


class CliError(ValueError):
    pass


def _root() -> Path:
    return Path.cwd().resolve()


def _emit(value: dict[str, object], args: argparse.Namespace) -> None:
    value = {"schema_version": "aieb.cli-output/v1", **value}
    if args.json:
        print(json.dumps(value, sort_keys=True))
    else:
        print(value.get("message", json.dumps(value, sort_keys=True)))


def _safe(value: object) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if any(token in str(key).lower() for token in ("secret", "token", "password", "api_key", "credential")):
                raise CliError("credentials are forbidden in local campaign/task data")
            _safe(child)
    elif isinstance(value, list):
        for child in value:
            _safe(child)


def _load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CliError(f"invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise CliError("document must be a JSON object")
    _safe(value)
    return value


def hash_tree(root: Path) -> str:
    """Deterministic content hash of every file under root, path-and-bytes
    keyed so a rename or a content edit both change the result."""
    entries = []
    for path in sorted(p for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
        entries.append(path.relative_to(root).as_posix().encode() + b"\0" + path.read_bytes())
    return hashlib.sha256(b"\0".join(entries)).hexdigest()


def hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _repo_root() -> Path:
    # packages/aieb-cli/src/aieb_cli/main.py -> repo root, independent of cwd
    # (evaluator source lives under tests/maintainer/, outside the task dir).
    return Path(__file__).resolve().parents[4]


def hash_evaluator_closure(evaluator_module: str) -> str:
    """Hash the evaluator's actual trusted dependency closure, not just its
    own file. A review found that hashing only `evaluator.py` let evaluator
    *behavior* change silently through code it imports without changing
    `evaluator_digest` at all - every evaluator but rag01's imports
    `tests/maintainer/common.py` (`CandidateProcess`, `request`), and rag01's
    own scoring logic lives partly in its sibling `fixture.py`. This hashes
    every `.py` file in the evaluator's own package directory plus
    `tests/maintainer/common.py` when it exists, so a change to either
    changes the digest."""
    root = _repo_root()
    package_dir = root.joinpath(*evaluator_module.split(".")[:-1])
    files = {p for p in package_dir.glob("*.py") if "__pycache__" not in p.parts}
    common = root / "tests" / "maintainer" / "common.py"
    if common.exists():
        files.add(common)
    entries = []
    for path in sorted(files, key=lambda p: p.relative_to(root).as_posix()):
        entries.append(path.relative_to(root).as_posix().encode() + b"\0" + path.read_bytes())
    return hashlib.sha256(b"\0".join(entries)).hexdigest()


def _runtime_descriptor(task: Path) -> tuple[str, str, str, str] | None:
    """Task-local runtime descriptor for tasks outside the curated development
    registry (e.g. real-source tasks under suites/real/). Carries the same
    4-tuple shape as a TASK_RUNTIMES entry, so a self-describing task needs no
    curated mapping entry; the curated 12 dev tasks never carry runtime.json."""
    path = task / "runtime.json"
    if not path.exists():
        return None
    value = _load_json(path)
    try:
        source_dir = str(value["source_dir"])
        evaluator_module = str(value["evaluator_module"])
        admission_module = str(value["admission_module"])
        admission_function = str(value["admission_function"])
    except KeyError as exc:
        raise CliError(f"runtime descriptor missing field: {exc.args[0]}") from exc
    if not source_dir or source_dir in {".", ".."} or "/" in source_dir or "\\" in source_dir:
        raise CliError("runtime descriptor source_dir must be a single package directory name")
    return (source_dir, evaluator_module, admission_module, admission_function)


def _runtime_for(task: Path, task_id: str) -> tuple[str, str, str, str]:
    runtime = TASK_RUNTIMES.get(task_id) or _runtime_descriptor(task)
    if runtime is None:
        raise CliError("task has no supported local evaluator")
    return runtime


def _verify_content_digests(task: Path, revision: TaskRevision) -> None:
    """Review finding #2: repository_digest/provenance_digest/contract_digest/
    service_topology_digest name real on-disk content this repo actually
    has (the task's repo/ tree, provenance.json, contracts/application-api.md,
    environment/README.md) - a task.yaml claiming a stale or hand-edited
    value for any of them must fail validation, not merely look like a
    plausible hex string. `environment.official_image`'s digest is excluded:
    it names a container image this project has never built or pushed, so
    there is no real content to check it against (see
    scripts/compute_task_digests.py for the fuller rationale)."""
    expected = {
        "repository_digest": (hash_tree(task / "repo"), revision.source.repository_digest),
        "provenance_digest": (hash_file(task / "provenance.json"), revision.source.provenance_digest),
        "contract_digest": (hash_file(task / "contracts" / "application-api.md"), revision.application.contract_digest),
        "service_topology_digest": (hash_file(task / "environment" / "README.md"), revision.environment.service_topology_digest),
    }
    runtime = TASK_RUNTIMES.get(revision.id)
    if runtime is None:
        runtime = _runtime_descriptor(task)
    if runtime is not None:
        expected["evaluator_digest"] = (hash_evaluator_closure(runtime[1]), revision.evaluator.evaluator_digest)
    mismatches = [field for field, (actual, claimed) in expected.items() if actual != claimed]
    if mismatches:
        raise CliError(
            "task.yaml digests do not match their on-disk content (stale or hand-edited): " + ", ".join(mismatches)
        )


def _task_check(task: Path) -> dict[str, object]:
    """Validate task.yaml against the canonical TaskRevision contract, not a
    hand-rolled subset of it. Every field TaskRevision defines - category/
    activity enums, egress_policy, digest formats, submission path safety,
    unique requirement IDs - is checked here; a task.yaml that would fail
    TaskRevision.model_validate() must fail `aieb task validate` too."""
    required = ("task.yaml", "instruction.md", "repo", "contracts/application-api.md", "provenance.json")
    missing = [name for name in required if not (task / name).exists()]
    if missing:
        raise CliError(f"task missing required files: {', '.join(missing)}")
    raw = yaml.safe_load((task / "task.yaml").read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise CliError("task.yaml must be a mapping")
    _safe(raw)
    try:
        revision = TaskRevision.model_validate(raw)
    except ValidationError as exc:
        raise CliError(f"task.yaml does not satisfy the TaskRevision contract: {exc}") from exc
    _verify_content_digests(task, revision)
    return {"task_id": revision.id, "task_version": revision.version, "requirements": len(revision.requirements)}


def _campaign_state(root: Path, campaign_id: str) -> Path:
    return root / ".aieb" / "runs" / campaign_id


@contextmanager
def _lock(state: Path):
    state.mkdir(parents=True, exist_ok=True)
    lock = state / "controller.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise CliError("local controller is already active; inspect or safely remove a stale lock") from exc
    try:
        os.write(descriptor, str(os.getpid()).encode("ascii"))
        yield
    finally:
        os.close(descriptor)
        lock.unlink(missing_ok=True)


def _freeze(root: Path, campaign_path: Path) -> tuple[Path, dict[str, object]]:
    campaign = _load_json(campaign_path)
    if campaign.get("schema_version") != "aieb.local-campaign/v1":
        raise CliError("unsupported campaign schema")
    campaign_id = campaign.get("id")
    if not isinstance(campaign_id, str) or not campaign_id:
        raise CliError("campaign requires ID")
    task_relative = campaign.get("task_dir")
    if not isinstance(task_relative, str):
        raise CliError("campaign requires task_dir")
    task = (root / task_relative).resolve()
    if root not in task.parents:
        raise CliError("task path must be inside repository")
    task_info = _task_check(task)
    candidate = campaign.get("candidate")
    if candidate not in {"baseline", "reference"}:
        raise CliError("currently supported local candidates are baseline and reference")
    frozen = {"schema_version": STATE_SCHEMA, "campaign": campaign, "campaign_digest": content_hash(campaign), "task": task_info}
    state = _campaign_state(root, campaign_id)
    state.mkdir(parents=True, exist_ok=True)
    manifest = state / "frozen-manifest.json"
    if manifest.exists() and json.loads(manifest.read_text(encoding="utf-8")) != frozen:
        raise CliError("existing frozen manifest differs; choose a new campaign ID")
    manifest.write_text(json.dumps(frozen, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return state, frozen


def _verify_frozen(root: Path, campaign_id: str, campaign_path: Path) -> tuple[Path, dict[str, object]]:
    state, expected = _freeze(root, campaign_path)
    if state != _campaign_state(root, campaign_id):
        raise CliError("campaign ID does not match frozen manifest")
    actual = _load_json(state / "frozen-manifest.json")
    if actual.get("campaign_digest") != expected.get("campaign_digest"):
        raise CliError("frozen manifest digest is invalid")
    return state, actual


def _editor(root: Path, task: Path, candidate: str, script: Path, source_dir: str) -> EngineeringCommand:
    if candidate == "baseline":
        script.write_text("pass\n", encoding="utf-8")
    else:
        reference = repr(str(task / "reference" / "backend.py"))
        script.write_text(
            "from pathlib import Path\nimport shutil\n"
            f"shutil.copyfile({reference}, Path.cwd() / {source_dir!r} / 'backend.py')\n",
            encoding="utf-8",
        )
    return EngineeringCommand((sys.executable, str(script)), 30)


def _run(root: Path, state: Path, frozen: dict[str, object]) -> dict[str, object]:
    campaign = frozen["campaign"]
    assert isinstance(campaign, dict)
    task = (root / str(campaign["task_dir"])).resolve()
    candidate = str(campaign["candidate"])
    # The currently supported development evaluator is repository-maintained
    # trusted material, deliberately outside the candidate task repository.
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    task_id = str(_task_check(task)["task_id"])
    runtime = _runtime_for(task, task_id)
    source_dir, evaluator_module, _, _ = runtime
    evaluate = importlib.import_module(evaluator_module).evaluate

    work = state / "work"
    script = state / "deterministic-editor.py"
    store = FilesystemArtifactStore(state / "artifacts")
    runner = LocalAttemptRunner(store)
    attempt_id = f"attempt-0-{uuid4().hex[:8]}"
    outcome = runner.run(
        AttemptConfig(
            attempt_id=attempt_id,
            frozen_source=task / "repo",
            work_root=work,
            base_revision_digest="1" * 64,
            submission=SubmissionPolicy(include=(f"{source_dir}/**",), protected=("dev_tests/**",), max_artifact_bytes=52_428_800),
            engineering=_editor(root, task, candidate, script, source_dir),
            access_scope=attempt_id,
        ),
        evaluate,
    )
    result = {
        "schema_version": "aieb.local-result/v1",
        "attempt_id": outcome.attempt_id,
        "execution_validity": outcome.execution_validity.value,
        "verdict": outcome.verdict.value if outcome.verdict else None,
        "attribution": outcome.attribution.value,
        "candidate_digest": outcome.candidate.manifest.digest() if outcome.candidate else None,
        "evaluation": outcome.evaluation,
        "evidence": str(outcome.evidence_path.relative_to(root)) if outcome.evidence_path else None,
    }
    (state / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with (state / "events.jsonl").open("a", encoding="utf-8") as events:
        events.write(json.dumps({"schema_version": "aieb.event/v1", "event_type": "attempt-finalized", "attempt_id": attempt_id, "verdict": result["verdict"]}, sort_keys=True) + "\n")
    return result


def _report(state: Path) -> Path:
    result = _load_json(state / "result.json")
    evaluation = result.get("evaluation") or {}
    checks = evaluation.get("checks", {}) if isinstance(evaluation, dict) else {}
    rows = "".join(f"<tr><td>{html.escape(str(key))}</td><td>{'PASS' if value else 'FAIL'}</td></tr>" for key, value in sorted(checks.items()))
    body = f"<!doctype html><meta charset=utf-8><title>AIEB local report</title><h1>AIEB local development report</h1><p>Verdict: {html.escape(str(result.get('verdict') or 'unavailable'))}</p><p>Engineering cost/resolution: unavailable (deterministic local entrant; no provider billing)</p><table><thead><tr><th>Requirement</th><th>Result</th></tr></thead><tbody>{rows}</tbody></table>"
    path = state / "report.html"
    path.write_text(body, encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aieb")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-color", action="store_true")
    parser.add_argument("--api-url", default=None)
    parser.add_argument("--access-token", default=None)
    parser.add_argument("--idempotency-key", default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    task = sub.add_parser("task").add_subparsers(dest="task_command", required=True)
    for name in ("validate", "verify", "admit"):
        command = task.add_parser(name); command.add_argument("directory", type=Path); command.add_argument("--candidate", default="reference")
    for name in ("plan", "run"):
        command = sub.add_parser(name); command.add_argument("--campaign", type=Path, required=True); command.add_argument("--fail-on-unsolved", action="store_true")
    resume = sub.add_parser("resume"); resume.add_argument("campaign_id"); resume.add_argument("--campaign", type=Path, required=True)
    inspect = sub.add_parser("inspect"); inspect.add_argument("--trial", required=True)
    report = sub.add_parser("report"); report.add_argument("--campaign", required=True); report.add_argument("--format", choices=("html",), default="html")
    operator_cli.add_operator_parsers(sub)
    args = parser.parse_args(argv)
    try:
        root = _root()
        if args.command == "operator":
            return operator_cli.dispatch(args)[0]
        if args.command == "doctor":
            _emit({"message": f"Python {platform.python_version()}; local deterministic capability available; real-agent capability blocked", "capabilities": {"local_rag01": True, "real_agent": False, "hard_cost_reservation": False}}, args); return 0
        if args.command == "task":
            info = _task_check(args.directory.resolve())
            if args.task_command == "admit":
                # Admission is maintainer tooling, intentionally separate from
                # publication. It reports evidence and leaves independent human
                # review pending; it never starts a campaign or publishes.
                repo = _repo_root()
                if str(repo) not in sys.path:
                    sys.path.insert(0, str(repo))
                from scripts.admit_task import inspect_task
                admission = inspect_task(args.directory.resolve())
                _emit({"message": "task admission evidence collected", **admission}, args); return 0
            if args.task_command == "verify":
                if args.candidate not in {"reference", "baseline"}: raise CliError("only baseline/reference supported")
                if str(root) not in sys.path:
                    sys.path.insert(0, str(root))
                task_id = str(info["task_id"])
                runtime = _runtime_for(args.directory.resolve(), task_id)
                _, _, admission_module, admission_function = runtime
                evaluate_variant = getattr(importlib.import_module(admission_module), admission_function)
                if admission_function == "run_matrix":
                    info["admission"] = next(row for row in evaluate_variant()["matrix"] if row["variant"] == args.candidate)
                else:
                    replacement = None if args.candidate == "baseline" else args.directory / "reference" / "backend.py"
                    info["admission"] = evaluate_variant(args.candidate, replacement)
            _emit({"message": "task validated", **info}, args); return 0
        if args.command == "plan":
            state, frozen = _freeze(root, args.campaign.resolve()); _emit({"message": "campaign frozen", "campaign_id": frozen["campaign"]["id"], "state": str(state.relative_to(root)), "digest": frozen["campaign_digest"]}, args); return 0
        if args.command in {"run", "resume"}:
            state, frozen = (_freeze(root, args.campaign.resolve()) if args.command == "run" else _verify_frozen(root, args.campaign_id, args.campaign.resolve()))
            with _lock(state): result = _run(root, state, frozen)
            _emit({"message": "local campaign completed", **result}, args)
            if result["execution_validity"] != "valid": return EXIT_INFRASTRUCTURE_INCOMPLETE
            return EXIT_UNSOLVED if getattr(args, "fail_on_unsolved", False) and result["verdict"] != "pass" else 0
        if args.command == "inspect":
            result = _load_json(_campaign_state(root, args.trial) / "result.json"); _emit({"message": "trial inspected", **result}, args); return 0
        if args.command == "report":
            path = _report(_campaign_state(root, args.campaign)); _emit({"message": "report generated", "report": str(path.relative_to(root))}, args); return 0
    except CliError as exc:
        _emit({"message": f"configuration error: {exc}"}, args); return EXIT_INVALID
    except KeyboardInterrupt:
        _emit({"message": "controller interrupted; resume requires matching frozen manifest"}, args); return 130
    except OSError as exc:
        _emit({"message": f"missing local capability: {exc}"}, args); return EXIT_MISSING_CAPABILITY
    return EXIT_INVALID
