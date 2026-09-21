"""v2 operator CLI: release/campaign/publication commands over the private API.

This parser branch is deliberately separate from the local deterministic
task runner in ``main.py``. Local commands never mutate a hosted campaign and
operator commands never touch the local runner. Operator commands talk ONLY
to authenticated private API operations; they never call a public website
route and never perform an unauthenticated mutation.

The API remains authoritative for every business rule. This module only:

- carries credentials and idempotency keys correctly;
- loads and verifies a frozen release/campaign manifest before planning/running;
- reports durable server state (never "success just because the HTTP request
  was accepted");
- exposes outstanding v2 blockers (V2-GAP-001, ENG-024, ...) instead of
  pretending a production-eligible run happened.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from aieb_core.models import ResolvedCampaign
from pydantic import ValidationError

from .private_api import OPERATOR_SCHEMA, ApiClientError, PrivateApiClient

EXIT_INVALID = 2

# Server campaign states that are anchored to a frozen manifest digest
# (freezing immutably pins manifest_digest/cohort_digest for the whole
# lifecycle). Unanchored states (draft/planned) have no manifest identity yet.
_MANIFEST_ANCHORED_STATES = ("frozen", "running", "completed", "incomplete")


class OperatorError(ValueError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def _require(value: str | None, flag: str) -> str:
    if not value or not str(value).strip():
        raise OperatorError("argument_required", f"{flag} is required")
    return str(value).strip()


def _client_from_args(args: argparse.Namespace) -> PrivateApiClient:
    api_url = getattr(args, "api_url", None) or os.environ.get("AIEB_API_URL")
    token = getattr(args, "access_token", None) or os.environ.get("AIEB_API_TOKEN")
    if not api_url:
        raise OperatorError("config_required", "an --api-url (or AIEB_API_URL) is required to reach the private API")
    if not str(api_url).startswith(("http://", "https://")):
        raise OperatorError("invalid_api_url", "api-url must be an absolute http(s) URL")
    return PrivateApiClient(base_url=str(api_url), access_token=str(token or ""))


def _envelope(command: str, request_id: str, *, data: dict | None = None, error: dict | None = None) -> dict:
    envelope = {
        "schema_version": OPERATOR_SCHEMA,
        "command": command,
        "status": "error" if error is not None else "ok",
        "request_id": request_id,
    }
    if data is not None:
        envelope["data"] = data
    if error is not None:
        envelope["error"] = error
    return envelope


def _error_object(
    code: str, message: str, retryable: bool, *, request_id: str | None = None, detail: object = None,
) -> dict:
    error = {"code": code, "message": message, "retryable": retryable}
    if request_id:
        error["request_id"] = request_id
    if detail is not None:
        error["server_response"] = detail
    return error


def _print(args: argparse.Namespace, envelope: dict) -> None:
    if args.json:
        print(json.dumps(envelope, sort_keys=True))
    elif envelope["status"] == "ok":
        message = envelope.get("data", {}).get("message", "ok")
        print(f"{envelope['command']}: {message}")
    else:
        err = envelope.get("error", {})
        print(f"{envelope['command']}: error ({err.get('code')}): {err.get('message')}", file=sys.stderr)


def _ok(
    args: argparse.Namespace, command: str, data: dict, *, request_id: str | None = None,
) -> tuple[int, dict]:
    envelope = _envelope(command, request_id or "local", data=data)
    _print(args, envelope)
    return 0, envelope


def _fail(
    args: argparse.Namespace, command: str, error: dict, *, request_id: str | None = None,
) -> tuple[int, dict]:
    envelope = _envelope(command, request_id or "local", error=error)
    _print(args, envelope)
    return EXIT_INVALID, envelope


def _load_manifest(path: str) -> ResolvedCampaign:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OperatorError("invalid_manifest", f"cannot read frozen manifest {path}: {exc}") from exc
    manifest = raw
    if isinstance(raw, dict) and not isinstance(raw.get("schema_version"), str) and isinstance(raw.get("resolved"), dict):
        # Allow a container that embeds the frozen campaign (e.g. a direct row dump).
        manifest = raw["resolved"]
    if not isinstance(manifest, dict) or manifest.get("schema_version") != "aieb.campaign/v1":
        raise OperatorError(
            "invalid_manifest",
            "frozen manifest must be an aieb.campaign/v1 resolved-campaign document",
        )
    try:
        return ResolvedCampaign.model_validate(manifest)
    except ValidationError as exc:
        raise OperatorError("invalid_manifest", f"frozen manifest failed the resolved-campaign contract: {exc}") from exc


def _frozen_manifest_digest(resolved: ResolvedCampaign) -> str:
    return resolved.digest()


def _get_campaign_with_digest(
    client: PrivateApiClient, campaign_id: str, resolved: ResolvedCampaign,
) -> tuple[dict, str]:
    """Send the frozen manifest's digest to the API and reject on any drift.

    The API's stored ``manifest_digest`` (and ``cohort_digest``) are the
    authoritative identities. Because a frozen campaign's digest covers its
    exact tasks/entrants/repetitions/protocol/budget/cohort, a mismatch here
    also rejects task/version/profile drift - the CLI never regenerates or
    mutates a frozen manifest.

    Enforcement applies once the server campaign is anchored to a frozen
    manifest (frozen/running/completed/incomplete). An unanchored campaign
    (draft/planned) is returned untouched so read-only ``plan`` reports the
    real state honestly; commands that require a manifest identity (run,
    release prepare) refuse it themselves.
    """
    digest = _frozen_manifest_digest(resolved)
    detail = client.request(
        "GET", f"/v1/campaigns/{campaign_id}",
        query={"expected_manifest_digest": digest},
    )
    campaign = detail.get("campaign") if isinstance(detail, dict) else None
    if not isinstance(campaign, dict):
        raise OperatorError("invalid_server_response", "campaign detail response did not include a campaign object")
    state = str(campaign.get("state") or "")
    server_manifest_digest = campaign.get("manifest_digest")
    server_cohort_digest = campaign.get("cohort_digest")
    if state in _MANIFEST_ANCHORED_STATES:
        if not isinstance(server_manifest_digest, str) or len(server_manifest_digest) != 64:
            raise OperatorError("manifest_not_frozen", "server campaign is anchored but holds no valid frozen manifest digest")
        if digest != server_manifest_digest:
            raise OperatorError(
                "manifest_digest_mismatch",
                f"frozen manifest digest {digest} differs from the server's stored digest "
                f"{server_manifest_digest}; refusing to proceed (never regenerate a frozen manifest - "
                "the supplied manifest no longer matches the server's frozen tasks/entrants/repetitions/protocol/budget)",
            )
        if not isinstance(server_cohort_digest, str) or len(server_cohort_digest) != 64:
            raise OperatorError(
                "cohort_drift",
                "server campaign is anchored to a frozen manifest but holds no valid cohort digest; refusing to proceed",
            )
        if server_cohort_digest != resolved.cohort.digest():
            raise OperatorError(
                "cohort_drift",
                "frozen manifest cohort digest differs from the server's cohort digest; refusing to proceed",
            )
    return detail, digest


def _blockers() -> list[dict]:
    """Outstanding v2 blockers the operator workflow cannot bypass."""
    return [
        {
            "gap": "V2-GAP-001",
            "status": "open",
            "blocker": "the hosted worker executes cells through the local runner bridge, not real leased-worker Harbor dispatch",
        },
        {
            "gap": "ENG-024",
            "status": "blocked",
            "blocker": "no authorized live campaign exists; official campaign/publication approval is an external gate",
        },
    ]


def _deadline_from_manifest(resolved: ResolvedCampaign) -> dict:
    budget = resolved.budget
    return {
        "engineer_wall_seconds": int(budget.engineer_wall_seconds),
        "verification_wall_seconds": int(budget.verification_wall_seconds),
        "note": "the hosted API models no campaign completion deadline; report the frozen per-trial wall-clock budget",
    }


def _cell_counts(resolved: ResolvedCampaign) -> dict:
    trials = list(resolved.trials)
    repetitions = max((int(trial.repetition_index) for trial in trials), default=-1) + 1
    return {
        "tasks": len(resolved.tasks),
        "entrants": len(resolved.entrants),
        "repetitions": repetitions,
        "trials": len(trials),
    }


def _gates_for(
    *,
    state: str,
    manifest_digest: str | None,
    local_digest: str,
    approved: bool,
    reservation: dict | None,
) -> list[dict]:
    frozen = state == "frozen"
    return [
        {
            "gate": "manifest_frozen",
            "satisfied": frozen and bool(manifest_digest),
            "detail": f"server campaign state is {state!r}" if not frozen else "frozen and has a manifest digest",
        },
        {
            "gate": "manifest_verified",
            "satisfied": bool(manifest_digest) and manifest_digest == local_digest,
            "detail": "local frozen digest matches the server's stored digest",
        },
        {
            "gate": "campaign_approved",
            "satisfied": bool(approved),
            "detail": "independent campaign approval is recorded" if approved else "campaign has not been approved",
        },
        {
            "gate": "budget_reservation_available",
            "satisfied": reservation is None or reservation.get("status") == "active",
            "detail": "reserved at start by the authoritative API" if reservation is None else f"reservation status is {reservation.get('status')!r}",
        },
    ]


# ---- release -----------------------------------------------------------------


def _release_dispatch(args: argparse.Namespace) -> tuple[int, dict]:
    client = _client_from_args(args)
    if args.operator_command == "prepare":
        return _release_prepare(args, client)
    if args.operator_command == "inspect":
        return _release_inspect(args, client)
    raise OperatorError("unknown_command", f"unknown release command {args.operator_command}")


def _release_prepare(args: argparse.Namespace, client: PrivateApiClient) -> tuple[int, dict]:
    command = "release.prepare"
    campaign_id = _require(getattr(args, "campaign", None), "--campaign")
    resolved = _load_manifest(_require(getattr(args, "manifest", None), "--manifest"))
    detail, local_digest = _get_campaign_with_digest(client, campaign_id, resolved)
    campaign = detail.get("campaign") if isinstance(detail, dict) else {}
    state = str(campaign.get("state") or "")
    if state not in ("completed", "incomplete"):
        code = "manifest_not_frozen" if state not in _MANIFEST_ANCHORED_STATES else "campaign_not_completed"
        raise OperatorError(
            code,
            f"release preparation requires a completed (or completed-with-incomplete-coverage) campaign "
            f"whose frozen manifest has been verified; the server state is {state!r}",
        )
    body: dict = {}
    publication_class = getattr(args, "publication_class", None)
    if publication_class:
        body["publication_class"] = publication_class
    supersedes = getattr(args, "supersedes", None)
    if supersedes:
        body["supersedes_publication_id"] = supersedes
    correction_reason = getattr(args, "correction_reason", None)
    if correction_reason:
        body["correction_reason"] = correction_reason
    response = client.request(
        "POST", f"/v1/campaigns/{campaign_id}/publications/prepare",
        body=body, idempotency_key=getattr(args, "idempotency_key", None),
    )
    data = {
        "message": f"release prepared for campaign {campaign_id}",
        "campaign_id": campaign_id,
        "manifest_id": str(resolved.id),
        "manifest_digest": local_digest,
        "server_manifest_digest": campaign.get("manifest_digest"),
        "cohort_digest": campaign.get("cohort_digest"),
        "digest_verified": (
            isinstance(campaign.get("manifest_digest"), str)
            and campaign.get("manifest_digest") == local_digest
        ),
        "campaign_state": state,
        "preparation": response,
    }
    return _ok(args, command, data, request_id=response.get("request_id"))


def _release_inspect(args: argparse.Namespace, client: PrivateApiClient) -> tuple[int, dict]:
    command = "release.inspect"
    preparation_id = _require(getattr(args, "preparation", None), "--preparation")
    response = client.request("GET", f"/v1/publications/preparations/{preparation_id}")
    preparation = response.get("preparation") if isinstance(response, dict) else None
    if isinstance(preparation, dict):
        message = f"prepared release {preparation_id} has status {preparation.get('status')}"
    else:
        message = f"prepared release {preparation_id} inspected"
    data = {
        "message": message,
        "preparation_id": preparation_id,
        "preparation": preparation,
        "snapshot_digest": response.get("preparation", {}).get("snapshot_digest") if isinstance(preparation, dict) else None,
        "evidence_manifest_digest": response.get("preparation", {}).get("evidence_manifest_digest") if isinstance(preparation, dict) else None,
        "can_approve": response.get("can_approve"),
        "approval_blocked_reason": response.get("approval_blocked_reason"),
        "correction_reason": response.get("correction_reason"),
    }
    return _ok(args, command, data, request_id=response.get("request_id"))


# ---- campaign ----------------------------------------------------------------


def _campaign_dispatch(args: argparse.Namespace) -> tuple[int, dict]:
    client = _client_from_args(args)
    if args.operator_command == "plan":
        return _campaign_plan(args, client)
    if args.operator_command == "run":
        return _campaign_run(args, client)
    if args.operator_command == "inspect":
        return _campaign_inspect(args, client)
    if args.operator_command == "approve":
        return _campaign_approve(args, client)
    raise OperatorError("unknown_command", f"unknown campaign command {args.operator_command}")


def _campaign_plan(args: argparse.Namespace, client: PrivateApiClient) -> tuple[int, dict]:
    command = "campaign.plan"
    campaign_id = _require(getattr(args, "campaign", None), "--campaign")
    resolved = _load_manifest(_require(getattr(args, "manifest", None), "--manifest"))
    detail, local_digest = _get_campaign_with_digest(client, campaign_id, resolved)
    campaign = detail["campaign"]
    progress = client.request("GET", f"/v1/campaigns/{campaign_id}/progress")
    approval = client.request("GET", f"/v1/campaigns/{campaign_id}/approval")
    state = str(campaign.get("state") or "")
    gates = _gates_for(
        state=state,
        manifest_digest=campaign.get("manifest_digest"),
        local_digest=local_digest,
        approved=bool(approval.get("approved")) if isinstance(approval, dict) else False,
        reservation=detail.get("reservation"),
    )
    data = {
        "message": f"campaign {campaign_id} planned (read-only; no mutation issued)",
        "campaign_id": campaign_id,
        "campaign_name": campaign.get("name"),
        "state": state,
        "frozen": state == "frozen",
        "manifest_id": str(resolved.id),
        "manifest_digest": local_digest,
        "server_manifest_digest": campaign.get("manifest_digest"),
        "digest_verified": bool(campaign.get("manifest_digest")) and campaign.get("manifest_digest") == local_digest,
        "cohort_id": resolved.cohort.id,
        "cohort_digest": campaign.get("cohort_digest"),
        "cells": _cell_counts(resolved),
        "planned_trials": progress.get("planned_trials"),
        "observed_trials": progress.get("observed_trials"),
        "deadline": _deadline_from_manifest(resolved),
        "budget_reservation": detail.get("reservation"),
        "approval": {"approved": approval.get("approved")} if isinstance(approval, dict) else {"approved": False},
        "gates": gates,
        "blockers": _blockers(),
        "mutation_issued": False,
    }
    return _ok(args, command, data, request_id=detail.get("request_id"))


def _campaign_run_gates(
    *,
    detail: dict,
    local_digest: str,
    approved: bool,
) -> str | None:
    campaign = detail.get("campaign") if isinstance(detail, dict) else None
    if not isinstance(campaign, dict):
        return "server returned no campaign state"
    state = str(campaign.get("state") or "")
    if state != "frozen":
        return f"the campaign is not frozen (state is {state!r}); only a frozen campaign may be run"
    manifest_digest = campaign.get("manifest_digest")
    if not manifest_digest or manifest_digest != local_digest:
        return "the campaign's frozen manifest digest could not be verified against the supplied manifest"
    if not approved:
        return "the campaign is not approved; approve it with `aieb operator campaign approve --campaign <id>` before running"
    reservation = detail.get("reservation") if isinstance(detail, dict) else None
    if isinstance(reservation, dict) and reservation.get("status") not in (None, "active"):
        return f"budget reservation is not active (status is {reservation.get('status')!r})"
    return None


def _campaign_run(args: argparse.Namespace, client: PrivateApiClient) -> tuple[int, dict]:
    command = "campaign.run"
    campaign_id = _require(getattr(args, "campaign", None), "--campaign")
    if not getattr(args, "confirm_run", False):
        raise OperatorError(
            "confirmation_required",
            "campaign run requires --confirm-run; this transactionally starts a frozen campaign",
        )
    resolved = _load_manifest(_require(getattr(args, "manifest", None), "--manifest"))
    detail, local_digest = _get_campaign_with_digest(client, campaign_id, resolved)
    approval = client.request("GET", f"/v1/campaigns/{campaign_id}/approval")
    approved = bool(approval.get("approved")) if isinstance(approval, dict) else False
    blocked = _campaign_run_gates(detail=detail, local_digest=local_digest, approved=approved)
    if blocked is not None:
        gate = {
            "not frozen": "not_frozen",
            "manifest": "manifest_digest_mismatch",
            "not approved": "approval_required",
            "budget reservation": "budget_reservation_unavailable",
        }
        code = "run_blocked"
        if "not approved" in blocked:
            code = "approval_required"
        elif "not frozen" in blocked:
            code = "not_frozen"
        elif "manifest digest" in blocked:
            code = "manifest_digest_mismatch"
        elif "budget reservation" in blocked:
            code = "budget_reservation_unavailable"
        _ = gate
        raise OperatorError(code, blocked)
    response = client.request(
        "POST", f"/v1/campaigns/{campaign_id}/start",
        body={}, idempotency_key=getattr(args, "idempotency_key", None),
    )
    campaign = response.get("campaign") if isinstance(response, dict) else {}
    states = _gates_for(
        state=str(campaign.get("state") or ""),
        manifest_digest=detail.get("campaign", {}).get("manifest_digest"),
        local_digest=local_digest,
        approved=approved,
        reservation=response.get("reservation"),
    )
    data = {
        "message": f"campaign {campaign_id} started; durable server state is {campaign.get('state')!r}",
        "campaign_id": campaign_id,
        "manifest_id": str(resolved.id),
        "manifest_digest": local_digest,
        "state": response,
        "gates": states,
        "blockers": _blockers(),
    }
    return _ok(args, command, data, request_id=response.get("request_id"))


def _campaign_inspect(args: argparse.Namespace, client: PrivateApiClient) -> tuple[int, dict]:
    command = "campaign.inspect"
    campaign_id = _require(getattr(args, "campaign", None), "--campaign")
    detail = client.request("GET", f"/v1/campaigns/{campaign_id}")
    progress = client.request("GET", f"/v1/campaigns/{campaign_id}/progress")
    invalid_attempts = client.request("GET", f"/v1/campaigns/{campaign_id}/invalid-attempts")
    approval = client.request("GET", f"/v1/campaigns/{campaign_id}/approval")
    campaign = detail.get("campaign") if isinstance(detail, dict) else {}
    data = {
        "message": f"campaign {campaign_id} inspected",
        "campaign_id": campaign_id,
        "campaign": campaign,
        "reservation": detail.get("reservation"),
        "progress": progress,
        "invalid_attempts": invalid_attempts if isinstance(invalid_attempts, list) else [],
        "approval": approval,
    }
    return _ok(args, command, data, request_id=detail.get("request_id"))


def _campaign_approve(args: argparse.Namespace, client: PrivateApiClient) -> tuple[int, dict]:
    command = "campaign.approve"
    campaign_id = _require(getattr(args, "campaign", None), "--campaign")
    body: dict = {}
    reason = getattr(args, "reason", None)
    if reason:
        body["reason"] = reason
    response = client.request(
        "POST", f"/v1/campaigns/{campaign_id}/approve",
        body=body, idempotency_key=getattr(args, "idempotency_key", None),
    )
    data = {
        "message": f"campaign {campaign_id} approval recorded"
        if response.get("approved")
        else f"campaign {campaign_id} is not approved",
        "campaign_id": campaign_id,
        "approval": response,
    }
    return _ok(args, command, data, request_id=response.get("request_id"))


# ---- publication -------------------------------------------------------------


def _publication_dispatch(args: argparse.Namespace) -> tuple[int, dict]:
    client = _client_from_args(args)
    if args.operator_command == "publish":
        return _publication_publish(args, client)
    raise OperatorError("unknown_command", f"unknown publication command {args.operator_command}")


def _publication_publish(args: argparse.Namespace, client: PrivateApiClient) -> tuple[int, dict]:
    command = "publication.publish"
    preparation_id = _require(getattr(args, "preparation", None), "--preparation")
    body: dict = {"decision": "approve"}
    if getattr(args, "independence_attestation", False):
        body["independence_attestation"] = True
    notes = getattr(args, "notes", None)
    if notes:
        body["notes"] = notes
    response = client.request(
        "POST", f"/v1/publications/preparations/{preparation_id}/review",
        body=body, idempotency_key=getattr(args, "idempotency_key", None),
    )
    published_id = response.get("published_publication_id") if isinstance(response, dict) else None
    message = (
        f"publication {published_id} is live (reviewer role, private review endpoint)"
        if published_id
        else "publication preparation approved"
    )
    data = {
        "message": message,
        "preparation_id": preparation_id,
        "review": response,
    }
    return _ok(args, command, data, request_id=response.get("request_id"))


# ---- parser + dispatch -------------------------------------------------------


def _common_flags(parser: argparse.ArgumentParser, *, muted_default: bool) -> None:
    default = argparse.SUPPRESS if muted_default else None
    parser.add_argument("--api-url", default=default)
    parser.add_argument("--access-token", default=default)
    parser.add_argument("--idempotency-key", default=default)


def add_operator_parsers(subparsers: argparse._SubParsersAction) -> None:
    """Register the `operator` namespace: operator release/campaign/publication."""
    operator = subparsers.add_parser("operator", help="private operator workflow against the authenticated API")
    area = operator.add_subparsers(dest="operator_area", required=True)

    release = area.add_parser("release", help="prepare and inspect a frozen release for a campaign")
    release_command = release.add_subparsers(dest="operator_command", required=True)
    prepare = release_command.add_parser("prepare", help="prepare a release for publication via the private API")
    _common_flags(prepare, muted_default=True)
    prepare.add_argument("--campaign", required=True)
    prepare.add_argument("--manifest", required=True, help="path to the frozen aieb.campaign/v1 manifest")
    prepare.add_argument("--publication-class", choices=("ranked", "non_ranked"), default=None)
    prepare.add_argument("--supersedes")
    prepare.add_argument("--correction-reason")
    inspect = release_command.add_parser("inspect", help="inspect a prepared release")
    _common_flags(inspect, muted_default=True)
    inspect.add_argument("--preparation", required=True)

    campaign = area.add_parser("campaign", help="plan, run, inspect, and approve a campaign")
    campaign_command = campaign.add_subparsers(dest="operator_command", required=True)
    plan = campaign_command.add_parser("plan", help="read-only frozen-manifest plan; makes no mutation")
    _common_flags(plan, muted_default=True)
    plan.add_argument("--campaign", required=True)
    plan.add_argument("--manifest", required=True, help="path to the frozen aieb.campaign/v1 manifest")
    run = campaign_command.add_parser("run", help="start a frozen, approved campaign; requires --confirm-run")
    _common_flags(run, muted_default=True)
    run.add_argument("--campaign", required=True)
    run.add_argument("--manifest", required=True, help="path to the frozen aieb.campaign/v1 manifest")
    run.add_argument("--confirm-run", action="store_true")
    inspect = campaign_command.add_parser("inspect", help="inspect campaign state, progress, approval, and invalid attempts")
    _common_flags(inspect, muted_default=True)
    inspect.add_argument("--campaign", required=True)
    approve = campaign_command.add_parser("approve", help="record independent campaign approval on the private API")
    _common_flags(approve, muted_default=True)
    approve.add_argument("--campaign", required=True)
    approve.add_argument("--reason")

    publication = area.add_parser("publication", help="publish a prepared release via the private review endpoint")
    publication_command = publication.add_subparsers(dest="operator_command", required=True)
    publish = publication_command.add_parser("publish", help="approve a prepared release into a live publication")
    _common_flags(publish, muted_default=True)
    publish.add_argument("--preparation", required=True)
    publish.add_argument("--independence-attestation", action="store_true")
    publish.add_argument("--notes")


def dispatch(args: argparse.Namespace) -> tuple[int, dict]:
    """Run an operator command and return (exit_code, envelope)."""
    handlers = {
        "release": _release_dispatch,
        "campaign": _campaign_dispatch,
        "publication": _publication_dispatch,
    }
    command = "operator"
    try:
        handler = handlers[args.operator_area]
    except KeyError as exc:
        raise OperatorError("unknown_command", f"unknown operator area {args.operator_area}") from exc
    try:
        return handler(args)
    except ApiClientError as exc:
        command = f"{args.operator_area}.{args.operator_command}"
        error = _error_object(
            exc.code, exc.message, exc.retryable,
            request_id=exc.request_id, detail=exc.detail,
        )
        return _fail(args, command, error, request_id=exc.request_id)
    except OperatorError as exc:
        command = f"{args.operator_area}.{args.operator_command}"
        return _fail(args, command, _error_object(exc.code, str(exc), exc.retryable))