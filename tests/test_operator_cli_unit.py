"""Unit tests for the v2 operator CLI and its private API client.

These tests never open a socket: a scripted fake ``RawTransport`` replaces the
transport inside ``PrivateApiClient`` so the CLI's guarantees (authorization,
idempotency, frozen-manifest digest identity, read-only planning, stable JSON
envelopes, no token leakage, private-route-only traffic) are exercised against
a fully controlled transport. Real-PostgreSQL behavior is covered separately
in ``test_operator_cli_integration.py``.
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
import uuid
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for src in (ROOT / "packages/aieb-cli/src", ROOT / "packages/aieb-core/src"):
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

from aieb_cli.main import main  # noqa: E402
from aieb_cli.private_api import ClientTransportError, RawResponse  # noqa: E402
from aieb_core.models import (  # noqa: E402
    Activity,
    ApplicationProfile,
    BudgetProfileV2,
    BudgetRole,
    Category,
    Cohort,
    DependencyMode,
    EntrantRevision,
    EnvironmentSpec,
    EvaluatorRef,
    ModelProfile,
    ProtocolRevision,
    Requirement,
    ResolvedCampaign,
    RoleBudget,
    SourceRef,
    SubmissionPolicy,
    TaskRevision,
    Track,
    Trial,
)

H64 = "a" * 64
BASE = "https://api.example.invalid"


def _repo_test_tmp() -> Path:
    """Repository test-temp convention (shared, gitignored, flat).

    OS-level temporary roots are not used for manifests because some managed
    Windows runners deny nested create/delete under process-owned directories;
    the repo cache dir plus a nested-create probe (below) gives a writable,
    diagnosable home instead of a mid-test PermissionError.
    """
    root = ROOT / ".cache" / "test-tmp"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _manifest_path_for(resolved: ResolvedCampaign) -> str:
    path = _repo_test_tmp() / f"frozen-{uuid.uuid4().hex}.json"
    path.write_text(json.dumps(resolved.model_dump(mode="json")), encoding="utf-8")
    return str(path)


def _probe_nested_test_tmp(test_case: unittest.TestCase) -> None:
    probe_root = _repo_test_tmp()
    probe: Path | None = None
    try:
        probe = Path(tempfile.mkdtemp(prefix=f"operator-probe-{uuid.uuid4().hex}-", dir=probe_root))
        child = probe / "child"
        child.mkdir()
        child.rmdir()
        probe.rmdir()
    except (OSError, PermissionError) as exc:
        test_case.skipTest(f"filesystem cannot create nested test directories under {probe_root}: {exc}")


def _task(version: str = "0.1.0") -> TaskRevision:
    return TaskRevision(
        schema_version="aieb.task/v1", id="rag.document-freshness", version=version, family_id="knowledge-service-a",
        category=Category.RAG, activity=Activity.REPAIR,
        source=SourceRef(repository_digest=H64, commit="synthetic", license="Apache-2.0", provenance_digest=H64),
        environment=EnvironmentSpec(official_image="registry.example/aieb@sha256:" + H64, engineer_cpu=1, engineer_memory_mb=512, service_topology_digest=H64, egress_policy="none"),
        application=ApplicationProfile(dependency_mode=DependencyMode.FIXTURE, entrypoint=("python", "-m", "srv"), contract_digest=H64, model_profile_id="m1"),
        submission=SubmissionPolicy(include=("src/",), protected=("tests/",), max_artifact_bytes=1_000_000),
        requirements=(Requirement(id="r1", severity="mandatory", description="works"),),
        evaluator=EvaluatorRef(evaluator_digest=H64, development_fixture="dev-v1", official_fixture_ref="maint:rag01-v1"),
        profile_compatibility=("cohort-a",),
    )


def _entrant() -> EntrantRevision:
    return EntrantRevision(
        schema_version="aieb.entrant/v1", id="agent-a", track=Track.AGENTS, agent_implementation="demo",
        agent_version="1.0.0", engineer_model=ModelProfile(provider_class="demo", requested_model="demo-model", settings_digest=H64),
        prompt_digest=H64, tools_digest=H64, capabilities=("cpu-fixture-standard-v1",), credential_ref_type="broker",
    )


def _resolved(*, task_version: str = "0.1.0", cohort_id: str = "cohort-a") -> ResolvedCampaign:
    task = _task(version=task_version)
    entrant = _entrant()
    budget = BudgetProfileV2(
        schema_version="aieb.budget/v2", id="budget-a", engineer_wall_seconds=1200, verification_wall_seconds=300,
        engineer_cpu=2, engineer_memory_mb=1024, environment_upper_bound_usd="0.5",
        per_role_budget_usd=(
            RoleBudget(role=BudgetRole.ENGINEER, limit_usd=None),
            RoleBudget(role=BudgetRole.DEV_APPLICATION, limit_usd=None),
            RoleBudget(role=BudgetRole.VERIFIER_APPLICATION, limit_usd=None),
            RoleBudget(role=BudgetRole.VERIFIER_JUDGE, limit_usd=None),
        ),
    )
    cohort = Cohort(
        schema_version="aieb.cohort/v1", id=cohort_id, track=Track.AGENTS, suite_id="suite-a",
        protocol_id="protocol-a", budget_profile_id="budget-a", dependency_mode=DependencyMode.FIXTURE,
        application_model_profile=task.application, hardware_class="cpu-fixture-standard-v1",
        required_capabilities=("cpu-fixture-standard-v1",),
    )
    protocol = ProtocolRevision(
        schema_version="aieb.protocol/v1", id="protocol-a", scoring_digest=H64, max_replacements=2,
        required_trace_coverage=False, hard_cost_ranking=False,
    )
    trial = Trial(
        id=uuid.uuid4(), campaign_digest=H64, task_digest=task.digest(), entrant_digest=entrant.digest(),
        cohort_digest=cohort.digest(), repetition_index=0, order_index=0,
    )
    return ResolvedCampaign(
        id=uuid.uuid4(), draft_digest=H64, cohort=cohort, protocol=protocol, budget=budget,
        tasks=(task,), entrants=(entrant,), trials=(trial,),
    )


def _json_response(status: int, payload: dict, request_id: str = "req-1") -> RawResponse:
    return RawResponse(
        status=status,
        headers={"content-type": "application/json", "x-request-id": request_id},
        body=json.dumps(payload).encode("utf-8"),
    )


def _campaign_body(resolved: ResolvedCampaign, *, campaign_id: str, state: str = "frozen") -> dict:
    return {
        "campaign": {
            "id": campaign_id, "name": "admin-campaign", "state": state, "revision": 2,
            "manifest_digest": resolved.digest(), "cohort_digest": resolved.cohort.digest(),
        },
        "reservation": None,
        "notice": None,
    }


def _progress_body(campaign_id: str) -> dict:
    return {
        "campaign_id": campaign_id, "state": "frozen", "planned_trials": 1, "observed_trials": 0,
        "attempts_by_phase": [], "attempts_by_terminal_status": [], "work_items_by_state": [],
    }


def _approval_body(campaign_id: str, *, approved: bool) -> dict:
    return {
        "campaign_id": campaign_id, "approved": approved, "approved_by_user_id": str(uuid.uuid4()) if approved else None,
        "approved_at": "2026-01-01T00:00:00Z" if approved else None, "reason": None,
        "review_id": str(uuid.uuid4()) if approved else None,
    }


class ScriptedTransport:
    """RawTransport stand-in: replies from a scripted queue, records every call."""

    def __init__(self, responses=()) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str, dict, bytes | None, float]] = []

    def request(self, method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float) -> RawResponse:
        self.calls.append((method, url, dict(headers), body, timeout))
        if not self._responses:
            raise AssertionError(f"unexpected request {method} {url}")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def paths(self) -> list[str]:
        return [self._relative(url).split("?")[0] for _, url, _, _, _ in self.calls]

    def methods(self) -> list[str]:
        return [method for method, _, _, _, _ in self.calls]

    def headers_for(self, method: str, path: str) -> dict:
        for call_method, url, headers, _, _ in self.calls:
            if call_method == method and self._relative(url).split("?")[0] == path:
                return headers
        return {}

    @staticmethod
    def _relative(url: str) -> str:
        if url.startswith(BASE):
            return url[len(BASE):]
        return url.split("/v1/", 1)[-1] if "/v1/" in url else url


def _run_cli(transport: ScriptedTransport, argv: list[str]) -> tuple[int, str, str]:
    def _factory(**_kwargs):  # replaced SocketTransport signature
        return transport
    clean_env = dict(os.environ)
    clean_env.pop("AIEB_API_URL", None)
    clean_env.pop("AIEB_API_TOKEN", None)
    env_patcher = mock.patch.dict(os.environ, clean_env)
    env_patcher.start()
    try:
        with mock.patch("aieb_cli.private_api.SocketTransport", _factory):
            with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
                code = main(argv)
    finally:
        env_patcher.stop()
    return code, out.getvalue(), err.getvalue()


def _parse_json(out: str) -> dict:
    return json.loads(out.strip().splitlines()[-1])


class OperatorCliAuthAndTransportTests(unittest.TestCase):
    """Authorization, idempotency, retry, redirect, and envelope guarantees."""

    def setUp(self) -> None:
        self.resolved = _resolved()
        self.manifest = self.resolved.model_dump(mode="json")
        self.campaign_id = str(self.resolved.id)
        _probe_nested_test_tmp(self)

    def test_missing_token_blocks_before_any_network_call(self) -> None:
        transport = ScriptedTransport()
        code, out, err = _run_cli(
            transport,
            ["--json", "--api-url", BASE, "operator", "campaign", "inspect", "--campaign", self.campaign_id],
        )
        self.assertEqual(code, 2)
        self.assertEqual(transport.calls, [])
        envelope = _parse_json(out)
        self.assertEqual(envelope["schema_version"], "aieb.operator-cli/v1")
        self.assertEqual(envelope["status"], "error")
        self.assertEqual(envelope["error"]["code"], "authorization_required")

    def test_missing_idempotency_key_never_sends_the_mutation(self) -> None:
        transport = ScriptedTransport([
            _json_response(200, _campaign_body(self.resolved, campaign_id=self.campaign_id, state="approved")),
            _json_response(200, _approval_body(self.campaign_id, approved=True)),
        ])
        manifest = _manifest_path_for(self.resolved)
        code, out, _ = _run_cli(
            transport,
            ["--json", "--api-url", BASE, "--access-token", "aaa", "operator", "campaign", "run",
             "--campaign", self.campaign_id, "--manifest", manifest, "--confirm-run"],
        )
        self.assertEqual(code, 2)
        self.assertNotIn("POST", transport.methods())
        self.assertEqual(_parse_json(out)["error"]["code"], "idempotency_key_required")

    def test_invalid_token_maps_to_authorization_denied_without_leakage(self) -> None:
        token = "secret-token-value"
        transport = ScriptedTransport([
            _json_response(401, {"error": {"code": "unauthorized", "message": "bad credentials"}, "request_id": "r1"}),
        ])
        code, out, err = _run_cli(
            transport,
            ["--json", "--api-url", BASE, "--access-token", token, "operator", "campaign", "inspect",
             "--campaign", self.campaign_id],
        )
        self.assertEqual(code, 2)
        envelope = _parse_json(out)
        self.assertEqual(envelope["error"]["code"], "authorization_denied")
        self.assertNotIn(token, out)
        self.assertNotIn(token, err)
        self.assertEqual(envelope["error"]["retryable"], False)

    def test_stale_version_maps_to_stale_version(self) -> None:
        transport = ScriptedTransport([
            _json_response(412, {"error": {"message": "stale revision"}}),
        ])
        code, out, _ = _run_cli(
            transport,
            ["--json", "--api-url", BASE, "--access-token", "aaa", "operator", "campaign", "inspect",
             "--campaign", self.campaign_id],
        )
        self.assertEqual(code, 2)
        self.assertEqual(_parse_json(out)["error"]["code"], "stale_version")

    def test_different_key_conflict_is_idempotency_conflict(self) -> None:
        transport = ScriptedTransport([
            _json_response(409, {"error": {"message": "Idempotency-Key reuse with a different request body"}}),
        ])
        code, out, _ = _run_cli(
            transport,
            ["--json", "--api-url", BASE, "--access-token", "aaa", "operator", "campaign", "approve",
             "--campaign", self.campaign_id, "--idempotency-key", "key-1"],
        )
        self.assertEqual(code, 2)
        envelope = _parse_json(out)
        self.assertEqual(envelope["error"]["code"], "idempotency_conflict")
        self.assertIn("server_response", envelope["error"])

    def test_mutation_sends_and_reuses_the_same_idempotency_key(self) -> None:
        responses = [
            _json_response(200, _approval_body(self.campaign_id, approved=True)),
            _json_response(200, _approval_body(self.campaign_id, approved=True)),
        ]
        transport = ScriptedTransport(responses)
        for _ in range(2):
            code, out, _ = _run_cli(
                transport,
                ["--json", "--api-url", BASE, "--access-token", "aaa", "operator", "campaign", "approve",
                 "--campaign", self.campaign_id, "--idempotency-key", "same-key"],
            )
            self.assertEqual(code, 0)
            self.assertEqual(_parse_json(out)["status"], "ok")
        headers = transport.headers_for("POST", f"/v1/campaigns/{self.campaign_id}/approve")
        self.assertEqual(headers["Idempotency-Key"], "same-key")
        self.assertEqual(len([c for c in transport.calls if c[0] == "POST"]), 2)

    def test_retry_replays_with_the_same_idempotency_key_only(self) -> None:
        transport = ScriptedTransport([
            ClientTransportError("socket timed out", safe_to_retry=True),
            _json_response(200, _approval_body(self.campaign_id, approved=True)),
        ])
        code, out, _ = _run_cli(
            transport,
            ["--json", "--api-url", BASE, "--access-token", "aaa", "operator", "campaign", "approve",
             "--campaign", self.campaign_id, "--idempotency-key", "retry-key"],
        )
        self.assertEqual(code, 0, _parse_json(out))
        posts = [call for call in transport.calls if call[0] == "POST"]
        self.assertEqual(len(posts), 2)
        self.assertEqual(posts[0][2]["Idempotency-Key"], "retry-key")
        self.assertEqual(posts[1][2]["Idempotency-Key"], "retry-key")

    def test_network_error_after_exhausted_attempts_is_retryable(self) -> None:
        transport = ScriptedTransport([
            ClientTransportError("connect refused", safe_to_retry=True),
            ClientTransportError("connect refused", safe_to_retry=True),
        ])
        code, out, _ = _run_cli(
            transport,
            ["--json", "--api-url", BASE, "--access-token", "aaa", "operator", "campaign", "inspect",
             "--campaign", self.campaign_id],
        )
        self.assertEqual(code, 2)
        envelope = _parse_json(out)
        self.assertEqual(envelope["error"]["code"], "network_error")
        self.assertEqual(envelope["error"]["retryable"], True)
        self.assertEqual(len(transport.calls), 2)

    def test_server_mutation_redirect_is_refused(self) -> None:
        transport = ScriptedTransport([
            RawResponse(status=307, headers={"location": "https://other.invalid/x"}, body=b""),
        ])
        code, out, _ = _run_cli(
            transport,
            ["--json", "--api-url", BASE, "--access-token", "aaa", "operator", "campaign", "approve",
             "--campaign", self.campaign_id, "--idempotency-key", "k"],
        )
        self.assertEqual(code, 2)
        self.assertEqual(_parse_json(out)["error"]["code"], "redirect_refused")
        self.assertEqual([call[0] for call in transport.calls], ["POST"])

    def test_503_maps_to_service_unavailable_without_retry(self) -> None:
        transport = ScriptedTransport([
            _json_response(503, {"error": {"message": "maintenance window"}}),
        ])
        code, out, _ = _run_cli(
            transport,
            ["--json", "--api-url", BASE, "--access-token", "aaa", "operator", "campaign", "inspect",
             "--campaign", self.campaign_id],
        )
        self.assertEqual(code, 2)
        envelope = _parse_json(out)
        self.assertEqual(envelope["error"]["code"], "service_unavailable")
        self.assertEqual(envelope["error"]["retryable"], True)
        self.assertEqual(len(transport.calls), 1)

    def test_every_private_call_carries_bearer_authorization(self) -> None:
        transport = ScriptedTransport([
            _json_response(200, _campaign_body(self.resolved, campaign_id=self.campaign_id)),
            _json_response(200, _progress_body(self.campaign_id)),
            _json_response(200, _approval_body(self.campaign_id, approved=False)),
        ])
        code, _, _ = _run_cli(
            transport,
            ["--json", "--api-url", BASE, "--access-token", "tok-1", "operator", "campaign", "plan",
             "--campaign", self.campaign_id, "--manifest", _manifest_path_for(self.resolved)],
        )
        self.assertEqual(code, 0)
        self.assertTrue(transport.calls)
        for _, _, headers, _, _ in transport.calls:
            self.assertEqual(headers["Authorization"], "Bearer tok-1")
            self.assertTrue(headers["Authorization"].startswith("Bearer "))

    def test_error_body_secrets_are_redacted_before_rendering(self) -> None:
        secrets = {
            "error": {"code": "boom", "message": "plain failure"},
            "token": "super-secret-token-value",
            "password": "password-hunter2",
            "credentials": {"api_key": "sk-abcdefghijklmnopqrstuvwx", "secret": "nested-secret-value"},
            "nested": {
                "authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.c2lnbmF0dXJlLXNpZ25hdHVyZQ.tG9rZW4",
                "note": "keep-me-plain",
            },
            "safe_key": "opaque-value",
        }
        transport = ScriptedTransport([_json_response(400, secrets)])
        code, out, err = _run_cli(
            transport,
            ["--json", "--api-url", BASE, "--access-token", "tok", "--idempotency-key", "k",
             "operator", "campaign", "approve", "--campaign", self.campaign_id],
        )
        self.assertEqual(code, 2)
        envelope = _parse_json(out)
        self.assertTrue(envelope["error"].get("server_response"))
        combined = out + err
        for secret in (
            "super-secret-token-value",
            "password-hunter2",
            "sk-abcdefghijklmnopqrstuvwx",
            "nested-secret-value",
            "eyJhbGciOiJIUzI1NiJ9.c2lnbmF0dXJlLXNpZ25hdHVyZQ.tG9rZW4",
        ):
            self.assertNotIn(secret, combined)
        rendered = json.dumps(envelope)
        self.assertIn("[REDACTED]", rendered)
        self.assertIn("keep-me-plain", rendered)
        self.assertIn("opaque-value", rendered)
        self.assertNotIn("super-secret-token-value", rendered)


class OperatorCliCommandTests(unittest.TestCase):
    """Each of the seven operator commands, using private API routes only."""

    def setUp(self) -> None:
        self.resolved = _resolved()
        self.manifest = self.resolved.model_dump(mode="json")
        self.campaign_id = str(self.resolved.id)
        _probe_nested_test_tmp(self)

    def test_campaign_plan_is_read_only_and_reports_verified_digest(self) -> None:
        transport = ScriptedTransport([
            _json_response(200, _campaign_body(self.resolved, campaign_id=self.campaign_id)),
            _json_response(200, _progress_body(self.campaign_id)),
            _json_response(200, _approval_body(self.campaign_id, approved=False)),
        ])
        code, out, _ = _run_cli(
            transport,
            ["--json", "--api-url", BASE, "--access-token", "tok", "operator", "campaign", "plan",
             "--campaign", self.campaign_id, "--manifest", _manifest_path_for(self.resolved)],
        )
        self.assertEqual(code, 0, out)
        envelope = _parse_json(out)
        self.assertEqual(envelope["schema_version"], "aieb.operator-cli/v1")
        self.assertEqual(envelope["command"], "campaign.plan")
        self.assertEqual(envelope["status"], "ok")
        data = envelope["data"]
        self.assertTrue(data["frozen"])
        self.assertTrue(data["digest_verified"])
        self.assertEqual(data["manifest_digest"], self.resolved.digest())
        self.assertFalse(data["mutation_issued"])
        self.assertEqual(data["planned_trials"], 1)
        self.assertEqual(data["cells"]["trials"], 1)
        self.assertEqual(transport.methods(), ["GET", "GET", "GET"])
        for path in transport.paths():
            self.assertTrue(path.startswith("/v1/campaigns/"))

    def test_campaign_run_gates_on_approval_and_never_starts_without_it(self) -> None:
        transport = ScriptedTransport([
            _json_response(200, _campaign_body(self.resolved, campaign_id=self.campaign_id, state="planned")),
            _json_response(200, _approval_body(self.campaign_id, approved=False)),
        ])
        code, out, _ = _run_cli(
            transport,
            ["--json", "--api-url", BASE, "--access-token", "tok", "--idempotency-key", "k",
             "operator", "campaign", "run", "--campaign", self.campaign_id,
             "--manifest", _manifest_path_for(self.resolved), "--confirm-run"],
        )
        self.assertEqual(code, 2)
        envelope = _parse_json(out)
        self.assertEqual(envelope["error"]["code"], "approval_required")
        self.assertNotIn("POST", transport.methods())

    def test_campaign_run_requires_confirm(self) -> None:
        transport = ScriptedTransport()
        code, out, _ = _run_cli(
            transport,
            ["--json", "--api-url", BASE, "--access-token", "tok", "--idempotency-key", "k",
             "operator", "campaign", "run", "--campaign", self.campaign_id,
             "--manifest", _manifest_path_for(self.resolved)],
        )
        self.assertEqual(code, 2)
        self.assertEqual(transport.calls, [])
        self.assertEqual(_parse_json(out)["error"]["code"], "confirmation_required")

    def test_manifest_digest_mismatch_blocks_run(self) -> None:
        detail = _campaign_body(self.resolved, campaign_id=self.campaign_id, state="approved")
        detail["campaign"]["manifest_digest"] = "f" * 64
        transport = ScriptedTransport([_json_response(200, detail)])
        code, out, _ = _run_cli(
            transport,
            ["--json", "--api-url", BASE, "--access-token", "tok", "--idempotency-key", "k",
             "operator", "campaign", "run", "--campaign", self.campaign_id,
             "--manifest", _manifest_path_for(self.resolved), "--confirm-run"],
        )
        self.assertEqual(code, 2)
        self.assertEqual(_parse_json(out)["error"]["code"], "manifest_digest_mismatch")
        self.assertNotIn("POST", transport.methods())

    def test_campaign_run_reports_success_with_durable_server_state(self) -> None:
        start = _campaign_body(self.resolved, campaign_id=self.campaign_id)
        start["campaign"]["state"] = "running"
        transport = ScriptedTransport([
            _json_response(200, _campaign_body(self.resolved, campaign_id=self.campaign_id, state="approved")),
            _json_response(200, _approval_body(self.campaign_id, approved=True)),
            _json_response(200, start),
        ])
        code, out, _ = _run_cli(
            transport,
            ["--json", "--api-url", BASE, "--access-token", "tok", "--idempotency-key", "start-1",
             "operator", "campaign", "run", "--campaign", self.campaign_id,
             "--manifest", _manifest_path_for(self.resolved), "--confirm-run"],
        )
        self.assertEqual(code, 0, out)
        envelope = _parse_json(out)
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(envelope["data"]["state"]["campaign"]["state"], "running")
        self.assertEqual(transport.methods(), ["GET", "GET", "POST"])
        post_headers = transport.headers_for("POST", f"/v1/campaigns/{self.campaign_id}/start")
        self.assertIn("Idempotency-Key", post_headers)

    def test_campaign_inspect_uses_only_private_reads(self) -> None:
        transport = ScriptedTransport([
            _json_response(200, _campaign_body(self.resolved, campaign_id=self.campaign_id)),
            _json_response(200, _progress_body(self.campaign_id)),
            _json_response(200, {"invalid_attempts": []}),
            _json_response(200, _approval_body(self.campaign_id, approved=False)),
        ])
        code, out, _ = _run_cli(
            transport,
            ["--json", "--api-url", BASE, "--access-token", "tok", "operator", "campaign", "inspect",
             "--campaign", self.campaign_id],
        )
        self.assertEqual(code, 0, out)
        self.assertEqual(transport.methods(), ["GET", "GET", "GET", "GET"])
        self.assertEqual(
            transport.paths(),
            [
                f"/v1/campaigns/{self.campaign_id}",
                f"/v1/campaigns/{self.campaign_id}/progress",
                f"/v1/campaigns/{self.campaign_id}/invalid-attempts",
                f"/v1/campaigns/{self.campaign_id}/approval",
            ],
        )

    def test_campaign_approve_posts_to_private_approval_route(self) -> None:
        body = _approval_body(self.campaign_id, approved=True)
        body["reason"] = "reviewed"
        transport = ScriptedTransport([_json_response(200, body)])
        code, out, _ = _run_cli(
            transport,
            ["--json", "--api-url", BASE, "--access-token", "tok", "--idempotency-key", "ap-1",
             "operator", "campaign", "approve", "--campaign", self.campaign_id, "--reason", "reviewed"],
        )
        self.assertEqual(code, 0, out)
        self.assertEqual(transport.paths(), [f"/v1/campaigns/{self.campaign_id}/approve"])
        self.assertEqual(transport.methods(), ["POST"])
        body_bytes = transport.calls[0][3]
        self.assertIsNotNone(body_bytes)
        self.assertEqual(json.loads(body_bytes)["reason"], "reviewed")

    def test_release_prepare_verifies_supplied_manifest_before_preparing(self) -> None:
        detail = _campaign_body(self.resolved, campaign_id=self.campaign_id, state="completed")
        transport = ScriptedTransport([
            _json_response(200, detail),
            _json_response(200, {"preparation_id": "prep-1", "status": "prepared"}),
        ])
        code, out, _ = _run_cli(
            transport,
            ["--json", "--api-url", BASE, "--access-token", "tok", "--idempotency-key", "rp-1",
             "operator", "release", "prepare", "--campaign", self.campaign_id,
             "--manifest", _manifest_path_for(self.resolved),
             "--publication-class", "ranked", "--correction-reason", "fix row"],
        )
        self.assertEqual(code, 0, out)
        self.assertEqual(
            transport.paths(),
            [
                f"/v1/campaigns/{self.campaign_id}",
                f"/v1/campaigns/{self.campaign_id}/publications/prepare",
            ],
        )
        self.assertEqual(transport.methods(), ["GET", "POST"])
        self.assertIn(
            f"expected_manifest_digest={self.resolved.digest()}",
            transport.calls[0][1],
        )
        headers = transport.headers_for("POST", f"/v1/campaigns/{self.campaign_id}/publications/prepare")
        self.assertIn("Idempotency-Key", headers)
        self.assertIn("Authorization", headers)
        data = _parse_json(out)["data"]
        self.assertEqual(data["manifest_digest"], self.resolved.digest())
        self.assertEqual(data["server_manifest_digest"], self.resolved.digest())
        self.assertEqual(data["cohort_digest"], self.resolved.cohort.digest())
        self.assertTrue(data["digest_verified"])
        self.assertEqual(data["campaign_state"], "completed")

    def test_release_prepare_refuses_stale_manifest_without_preparing(self) -> None:
        detail = _campaign_body(self.resolved, campaign_id=self.campaign_id, state="completed")
        detail["campaign"]["manifest_digest"] = "f" * 64
        transport = ScriptedTransport([_json_response(200, detail)])
        code, out, _ = _run_cli(
            transport,
            ["--json", "--api-url", BASE, "--access-token", "tok", "--idempotency-key", "rp-1",
             "operator", "release", "prepare", "--campaign", self.campaign_id,
             "--manifest", _manifest_path_for(self.resolved)],
        )
        self.assertEqual(code, 2)
        self.assertEqual(_parse_json(out)["error"]["code"], "manifest_digest_mismatch")
        self.assertEqual(transport.methods(), ["GET"])

    def test_release_prepare_refuses_changed_task_revision_without_preparing(self) -> None:
        server_version = _resolved(task_version="0.2.0")
        self.assertNotEqual(server_version.digest(), self.resolved.digest(), "task revision must change the digest")
        detail = _campaign_body(server_version, campaign_id=self.campaign_id, state="completed")
        transport = ScriptedTransport([_json_response(200, detail)])
        code, out, _ = _run_cli(
            transport,
            ["--json", "--api-url", BASE, "--access-token", "tok", "--idempotency-key", "rp-1",
             "operator", "release", "prepare", "--campaign", self.campaign_id,
             "--manifest", _manifest_path_for(self.resolved)],
        )
        self.assertEqual(code, 2)
        envelope = _parse_json(out)
        self.assertEqual(envelope["error"]["code"], "manifest_digest_mismatch")
        self.assertIn(self.resolved.digest(), envelope["error"]["message"])
        self.assertEqual(transport.methods(), ["GET"])

    def test_release_prepare_refuses_changed_cohort_without_preparing(self) -> None:
        detail = _campaign_body(self.resolved, campaign_id=self.campaign_id, state="completed")
        detail["campaign"]["cohort_digest"] = "e" * 64
        transport = ScriptedTransport([_json_response(200, detail)])
        code, out, _ = _run_cli(
            transport,
            ["--json", "--api-url", BASE, "--access-token", "tok", "--idempotency-key", "rp-1",
             "operator", "release", "prepare", "--campaign", self.campaign_id,
             "--manifest", _manifest_path_for(self.resolved)],
        )
        self.assertEqual(code, 2)
        self.assertEqual(_parse_json(out)["error"]["code"], "cohort_drift")
        self.assertEqual(transport.methods(), ["GET"])

    def test_release_prepare_refuses_missing_or_malformed_cohort_digest(self) -> None:
        for mutate in ("pop", "malform"):
            detail = _campaign_body(self.resolved, campaign_id=self.campaign_id, state="completed")
            if mutate == "pop":
                detail["campaign"].pop("cohort_digest")
            else:
                detail["campaign"]["cohort_digest"] = "not-a-digest"
            transport = ScriptedTransport([_json_response(200, detail)])
            code, out, _ = _run_cli(
                transport,
                ["--json", "--api-url", BASE, "--access-token", "tok", "--idempotency-key", "rp-1",
                 "operator", "release", "prepare", "--campaign", self.campaign_id,
                 "--manifest", _manifest_path_for(self.resolved)],
            )
            self.assertEqual(code, 2, mutate)
            self.assertEqual(_parse_json(out)["error"]["code"], "cohort_drift", mutate)
            self.assertEqual(transport.methods(), ["GET"], mutate)

    def test_release_prepare_refuses_non_frozen_campaign_without_preparing(self) -> None:
        detail = _campaign_body(self.resolved, campaign_id=self.campaign_id, state="planned")
        detail["campaign"].pop("manifest_digest")
        detail["campaign"].pop("cohort_digest")
        transport = ScriptedTransport([_json_response(200, detail)])
        code, out, _ = _run_cli(
            transport,
            ["--json", "--api-url", BASE, "--access-token", "tok", "--idempotency-key", "rp-1",
             "operator", "release", "prepare", "--campaign", self.campaign_id,
             "--manifest", _manifest_path_for(self.resolved)],
        )
        self.assertEqual(code, 2)
        envelope = _parse_json(out)
        self.assertEqual(envelope["error"]["code"], "manifest_not_frozen")
        self.assertEqual(transport.methods(), ["GET"])

    def test_release_inspect_reads_private_preparation_detail(self) -> None:
        transport = ScriptedTransport([
            _json_response(200, {"preparation": {"id": "prep-1", "status": "prepared"}, "can_approve": True}),
        ])
        code, out, _ = _run_cli(
            transport,
            ["--json", "--api-url", BASE, "--access-token", "tok", "operator", "release", "inspect",
             "--preparation", "prep-1"],
        )
        self.assertEqual(code, 0, out)
        self.assertEqual(transport.paths(), ["/v1/publications/preparations/prep-1"])
        self.assertEqual(transport.methods(), ["GET"])
        envelope = _parse_json(out)
        self.assertEqual(envelope["data"]["preparation"]["status"], "prepared")

    def test_publication_publish_uses_private_review_endpoint_only(self) -> None:
        transport = ScriptedTransport([
            _json_response(200, {"published_publication_id": "pub-1", "review_kind": "independent"}),
        ])
        code, out, _ = _run_cli(
            transport,
            ["--json", "--api-url", BASE, "--access-token", "tok", "--idempotency-key", "pub-1",
             "operator", "publication", "publish", "--preparation", "prep-1", "--independence-attestation"],
        )
        self.assertEqual(code, 0, out)
        self.assertEqual(transport.paths(), ["/v1/publications/preparations/prep-1/review"])
        self.assertEqual(transport.methods(), ["POST"])
        headers = transport.headers_for("POST", "/v1/publications/preparations/prep-1/review")
        self.assertIn("Idempotency-Key", headers)
        self.assertEqual(json.loads(transport.calls[0][3])["decision"], "approve")
        self.assertEqual(json.loads(transport.calls[0][3])["independence_attestation"], True)

    def test_publication_publish_requires_idempotency_key_before_network(self) -> None:
        transport = ScriptedTransport()
        code, out, _ = _run_cli(
            transport,
            ["--json", "--api-url", BASE, "--access-token", "tok",
             "operator", "publication", "publish", "--preparation", "prep-1"],
        )
        self.assertEqual(code, 2)
        self.assertEqual(transport.calls, [])
        self.assertEqual(_parse_json(out)["error"]["code"], "idempotency_key_required")

    def test_campaign_plan_refuses_anchored_campaign_digest_mismatch(self) -> None:
        detail = _campaign_body(self.resolved, campaign_id=self.campaign_id, state="running")
        detail["campaign"]["manifest_digest"] = "f" * 64
        transport = ScriptedTransport([_json_response(200, detail)])
        code, out, _ = _run_cli(
            transport,
            ["--json", "--api-url", BASE, "--access-token", "tok", "operator", "campaign", "plan",
             "--campaign", self.campaign_id, "--manifest", _manifest_path_for(self.resolved)],
        )
        self.assertEqual(code, 2)
        self.assertEqual(_parse_json(out)["error"]["code"], "manifest_digest_mismatch")
        self.assertEqual(transport.methods(), ["GET"])

    def test_campaign_plan_reports_non_anchored_campaign_without_pretending(self) -> None:
        # A draft has no frozen manifest identity at all; plan must report it
        # honestly (not frozen, digests unverified) instead of pretending.
        detail = _campaign_body(self.resolved, campaign_id=self.campaign_id, state="draft")
        detail["campaign"].pop("manifest_digest")
        detail["campaign"].pop("cohort_digest")
        transport = ScriptedTransport([
            _json_response(200, detail),
            _json_response(200, _progress_body(self.campaign_id)),
            _json_response(200, _approval_body(self.campaign_id, approved=False)),
        ])
        code, out, _ = _run_cli(
            transport,
            ["--json", "--api-url", BASE, "--access-token", "tok", "operator", "campaign", "plan",
             "--campaign", self.campaign_id, "--manifest", _manifest_path_for(self.resolved)],
        )
        self.assertEqual(code, 0, out)
        envelope = _parse_json(out)
        self.assertEqual(envelope["data"]["state"], "draft")
        self.assertFalse(envelope["data"]["frozen"])
        self.assertFalse(envelope["data"]["digest_verified"])
        self.assertFalse(envelope["data"]["mutation_issued"])


if __name__ == "__main__":
    unittest.main()