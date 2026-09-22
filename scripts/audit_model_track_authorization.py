"""Audit readiness for an authorized real model-track campaign.

The audit is deliberately read-only. It never contacts a provider and never
prints credential values. A plan can be prepared without authorization, but a
real campaign must not proceed until every reported gate is true.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from decimal import Decimal, InvalidOperation
from pathlib import Path

from aieb_core.contracts_v2 import ModelExecutionAuthorization

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PLAN = ROOT / "docs" / "implementation" / "evidence" / "V2-GAP-009" / "model-execution-plan.json"
LOOP_PATH = ROOT / "packages" / "aieb-runner" / "src" / "aieb_runner" / "model_loop.py"
ZERO_DIGEST = "0" * 64


def _frozen_loop_digests() -> dict[str, str]:
    source = LOOP_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(LOOP_PATH))
    env: dict[str, object] = {}
    values: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            if target.id in {
                "MAX_READ_BYTES", "MAX_COMMAND_TIMEOUT_SEC", "MAX_TOOL_RESULT_CHARS",
                "MAX_COMMAND_OUTPUT_CHARS", "MAX_STEPS", "MAX_RETRIES_PER_STEP",
                "MAX_CONTEXT_CHARS", "STEP_RETRY_BACKOFF_SECONDS",
            }:
                env[target.id] = ast.literal_eval(node.value)
            if target.id in {"SYSTEM_PROMPT", "TOOL_SCHEMAS"}:
                values[target.id] = eval(compile(ast.Expression(node.value), str(LOOP_PATH), "eval"), {}, env)
    return {
        "fixed_loop_source_digest": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "system_prompt_digest": hashlib.sha256(str(values["SYSTEM_PROMPT"]).encode("utf-8")).hexdigest(),
        "tool_schema_digest": hashlib.sha256(
            json.dumps(values["TOOL_SCHEMAS"], sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
    }


def _positive_decimal(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        amount = Decimal(value)
    except (InvalidOperation, ValueError):
        return False
    return amount.is_finite() and amount > 0


def _verify_evidence_ref(value: object, label: str) -> tuple[bool, str | None]:
    """Verify a local evidence URI, including containment and SHA-256.

    A ``private://`` URI is deliberately *not* treated as verified by this
    local audit: without an authenticated holdout/evidence reader, accepting
    a non-empty private reference would turn an assertion into fake proof.
    """
    if not isinstance(value, str) or not value.strip():
        return False, f"{label} is absent"
    if value.startswith("private://"):
        return False, f"{label} points to private evidence unavailable to this audit"
    if not value.startswith("evidence://"):
        return False, f"{label} must use evidence://path#sha256"
    body = value[len("evidence://"):]
    if "#" not in body:
        return False, f"{label} is missing its digest fragment"
    relative, expected = body.rsplit("#", 1)
    if len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
        return False, f"{label} has a malformed SHA-256 digest"
    if not relative or relative.startswith(("/", "\\")):
        return False, f"{label} path must be repository-relative"
    candidate = (ROOT / Path(relative)).resolve()
    try:
        candidate.relative_to(ROOT.resolve())
    except ValueError:
        return False, f"{label} escapes the repository"
    if not candidate.is_file():
        return False, f"{label} file does not exist"
    actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
    if actual != expected:
        return False, f"{label} digest does not match the referenced file"
    return True, None


def _bound_evidence_ref(value: object) -> bool:
    """Compatibility predicate retained for callers; unlike the old version,
    only a readable, digest-bound local evidence URI returns true.
    """
    return _verify_evidence_ref(value, "evidence reference")[0]


def audit(plan_path: Path = DEFAULT_PLAN) -> dict[str, object]:
    errors: list[str] = []
    plan = json.loads(plan_path.read_text(encoding="utf-8")) if plan_path.is_file() else {}
    if not plan:
        errors.append("execution plan is missing")
    if plan.get("track") != "models":
        errors.append("plan is not a model-track plan")
    if not isinstance(plan.get("provider_id"), str) or not plan["provider_id"].strip():
        errors.append("provider identity is missing")
    if not isinstance(plan.get("cohort_id"), str) or not str(plan["cohort_id"]).startswith("mvp2-model-"):
        errors.append("model track requires a separate mvp2-model-* cohort")
    actual = _frozen_loop_digests()
    fixed_contract = all(plan.get(name) == value for name, value in actual.items())
    if not fixed_contract:
        errors.append("fixed reference loop/prompt/tool-schema digest mismatch")
    if plan.get("protocol_digest") in (None, ZERO_DIGEST):
        errors.append("protocol digest is missing or a placeholder")
    requested_models = plan.get("requested_models")
    if not isinstance(requested_models, list) or not requested_models or any(
        not isinstance(model, str) or not model.strip() or "pending" in model.lower() for model in requested_models
    ):
        errors.append("requested model identities are not concrete and frozen")
    unsupported = plan.get("unsupported_controls")
    if not isinstance(unsupported, list) or not all(isinstance(item, str) and item.strip() for item in unsupported):
        errors.append("unsupported provider controls are not disclosed")
    credential_env_var = plan.get("credential_env_var")
    credential_present = bool(isinstance(credential_env_var, str) and os.environ.get(credential_env_var))
    if not credential_present:
        errors.append("authorized provider credential is not configured")
    spend_cap_ready = _positive_decimal(plan.get("spend_cap_usd"))
    if not spend_cap_ready:
        errors.append("approved positive spend cap is missing")
    authorization_ready = False
    authorization = plan.get("authorization")
    if authorization is not None:
        try:
            record = ModelExecutionAuthorization.model_validate(authorization)
            authorization_ready = (
                record.cohort_id == plan.get("cohort_id")
                and record.provider_id == plan.get("provider_id")
                and record.credential_env_var == credential_env_var
                and set(record.requested_models) == set(requested_models or [])
                and record.spend_cap_usd == str(plan.get("spend_cap_usd"))
            )
        except Exception as exc:
            errors.append(f"authorization record is invalid: {exc}")
    else:
        errors.append("no provider/model authorization record exists")
    if not authorization_ready and authorization is not None:
        errors.append("authorization record does not bind the planned cohort/models")
    usage_identity_ready, usage_error = _verify_evidence_ref(
        plan.get("usage_identity_evidence_ref"), "usage/model-identity evidence"
    )
    if not usage_identity_ready:
        errors.append(usage_error or "real usage/model-identity persistence evidence is unverifiable")
    real_call_ready, call_error = _verify_evidence_ref(
        plan.get("real_provider_call_evidence_ref"), "real provider call evidence"
    )
    if not real_call_ready:
        errors.append(call_error or "real provider call evidence is unverifiable")
    return {
        "schema_version": "aieb.model-track-readiness/v1",
        "plan_status": plan.get("status", "missing"),
        "cohort_id": plan.get("cohort_id"),
        "provider_id": plan.get("provider_id"),
        "requested_models": requested_models or [],
        "fixed_contract_verified": fixed_contract,
        "credential_present": credential_present,
        "spend_cap_ready": spend_cap_ready,
        "authorization_ready": authorization_ready,
        "usage_identity_persistence_ready": usage_identity_ready,
        "real_provider_call_executed": real_call_ready,
        "errors": errors,
        "ready_for_real_execution": not errors,
        "official_release_eligible": False,
        "reason": "Read-only readiness audit; no provider call is made by this command.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(args.plan)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    print(rendered, end="")
    if args.output:
        output = args.output if args.output.is_absolute() else ROOT / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
