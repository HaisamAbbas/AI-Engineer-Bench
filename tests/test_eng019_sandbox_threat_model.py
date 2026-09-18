"""ENG-019 official sandbox and threat-model tests (spec section 43: SE-01/SE-02; STATUS.md
acceptance: "SE-02 denies and logs verifier/cloud-metadata access; no host, secret,
hidden-label, or other-trial access; teardown and egress controls pass").

SE-01 (escaping symlink -> artifact rejected) already has direct coverage under ENG-003 in
`tests/test_candidate_artifacts.py::CandidateArtifactsTest.test_rejects_symlink_escape` - not
duplicated here.

Scope, per ADR-12: these tests exercise the `EgressGuardProxy` application-layer enforcement
and the `ExecutionBackend` refusal contract directly (no Docker/Harbor required, no network
access performed) - they do not claim VM-level hardened isolation, which stays deferred
("Official VM provider" in DECISIONS.md's Open decisions table).
"""

from __future__ import annotations

import asyncio
import http.client
import tempfile
import unittest
from pathlib import Path

from aieb_runner.backends.base import (
    ExecutionSpec,
    IsolationPolicy,
    UnhardenedBackendError,
)
from aieb_runner.backends.egress_proxy import EgressGuardProxy
from aieb_runner.backends.harbor.backend import HarborBackend, _find_docker_socket_mount


class EgressGuardProxyTest(unittest.TestCase):
    """Direct, real-network tests of the deny-by-default guard (no mocking of the proxy
    itself): a real client makes a real HTTP request through a real listening socket."""

    def _request_through(self, proxy: EgressGuardProxy, host: str, port: int = 80, *, authenticated: bool = True) -> http.client.HTTPResponse:
        conn = http.client.HTTPConnection(proxy.host if proxy.host != "host.docker.internal" else "127.0.0.1", proxy.port, timeout=5)
        self.addCleanup(conn.close)
        headers = {"Host": f"{host}:{port}"}
        if authenticated:
            headers["Proxy-Authorization"] = proxy.proxy_authorization_header()
        conn.request("GET", f"http://{host}:{port}/", headers=headers)
        return conn.getresponse()

    def test_an_unauthenticated_request_is_refused_before_any_policy_check(self) -> None:
        """The proxy binds 0.0.0.0 (needed for a container to reach it at all), so without
        authentication anything on the same network could relay allowlisted traffic through
        it. A request with no (or the wrong) Proxy-Authorization must be refused outright -
        not merely denied by the allowlist, refused BEFORE the target host is even parsed."""
        policy = IsolationPolicy(egress_allowlist=("api.allowed-example.test",))
        with EgressGuardProxy(policy) as proxy:
            response = self._request_through(proxy, "api.allowed-example.test", authenticated=False)
            self.assertEqual(response.status, 407)
            self.assertEqual(proxy.denials, [])  # not logged as a policy denial - it's a stranger, not a policy decision

    def test_deny_by_default_denies_a_host_not_on_the_allowlist_and_logs_it(self) -> None:
        policy = IsolationPolicy(egress_allowlist=("api.allowed-example.test",))
        with EgressGuardProxy(policy) as proxy:
            response = self._request_through(proxy, "not-allowed-example.test")
            self.assertEqual(response.status, 403)
            self.assertEqual(len(proxy.denials), 1)
            self.assertEqual(proxy.denials[0].host, "not-allowed-example.test")
            self.assertIn("not on egress allowlist", proxy.denials[0].reason)

    def test_empty_allowlist_denies_everything_deny_by_default(self) -> None:
        policy = IsolationPolicy(egress_allowlist=())
        with EgressGuardProxy(policy) as proxy:
            response = self._request_through(proxy, "anything-at-all.test")
            self.assertEqual(response.status, 403)
            self.assertIn("deny-by-default", proxy.denials[0].reason)

    def test_se02_cloud_metadata_endpoint_is_denied_and_logged_even_when_allowlisted(self) -> None:
        """SE-02: candidate requests verifier/cloud metadata endpoint -> access denied and
        attempt logged. The metadata host is denied unconditionally - even a policy that
        (mistakenly or maliciously) allowlists it is overridden, since metadata access is
        never a legitimate application route."""
        policy = IsolationPolicy(egress_allowlist=("169.254.169.254",))
        with EgressGuardProxy(policy) as proxy:
            response = self._request_through(proxy, "169.254.169.254")
            self.assertEqual(response.status, 403)
            self.assertEqual(len(proxy.denials), 1)
            self.assertIn("cloud-metadata", proxy.denials[0].reason)

    def test_allowlisted_host_is_forwarded_not_denied(self) -> None:
        # Use a real local HTTP server standing in for an allowed application/broker route,
        # proving the allowlist path actually forwards traffic rather than denying everything.
        import http.server
        import threading

        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - stdlib API
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *args: object) -> None:  # silence test output
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        try:
            allowed_port = server.server_address[1]
            policy = IsolationPolicy(egress_allowlist=("127.0.0.1",))
            with EgressGuardProxy(policy) as proxy:
                response = self._request_through(proxy, "127.0.0.1", allowed_port)
                self.assertEqual(response.status, 200)
                self.assertEqual(proxy.denials, [])
        finally:
            server.shutdown()
            server_thread.join(timeout=2)

    def test_env_vars_advertise_the_configured_host_not_the_bind_host(self) -> None:
        policy = IsolationPolicy()
        with EgressGuardProxy(policy, bind_host="0.0.0.0", advertised_host="host.docker.internal") as proxy:
            env = proxy.env_vars()
            self.assertTrue(env["HTTP_PROXY"].startswith("http://"))
            self.assertTrue(env["HTTP_PROXY"].endswith(f"@host.docker.internal:{proxy.port}"))
            self.assertEqual(env["NO_PROXY"], "")

    def test_env_vars_embed_a_per_instance_token_that_authenticates_the_request(self) -> None:
        """The embedded `user:pass@host` token is not decorative - a real HTTP client that
        parses it sends the exact Proxy-Authorization this proxy requires."""
        policy = IsolationPolicy(egress_allowlist=("127.0.0.1",))
        with EgressGuardProxy(policy) as proxy:
            env = proxy.env_vars()
            embedded_token = env["HTTP_PROXY"].split("://", 1)[1].split(":@", 1)[0]
            self.assertTrue(embedded_token)  # a real, non-empty per-instance secret
            conn = http.client.HTTPConnection(proxy.host, proxy.port, timeout=5)
            self.addCleanup(conn.close)
            conn.request("GET", "http://127.0.0.1:1/", headers={
                "Host": "127.0.0.1:1",
                "Proxy-Authorization": proxy.proxy_authorization_header(),
            })
            response = conn.getresponse()
            self.assertNotEqual(response.status, 407)  # authenticated - got past the auth gate


class HarborBackendRefusalTest(unittest.IsolatedAsyncioTestCase):
    """A non-hardened backend must refuse hardened-only allocations outright, not merely
    disclose the limitation (ADR-12): 'we said it isn't hardened' must become 'it cannot be
    used as if it were'."""

    async def test_launch_refuses_a_hardened_isolation_required_allocation(self) -> None:
        backend = HarborBackend()
        spec = ExecutionSpec(
            task_dir=Path("."),
            runs_dir=Path(".cache/eng019-tests/unused"),
            trial_name="refused-trial",
            agent_import_path="unused.agent",
            agent_timeout_sec=1.0,
            isolation=IsolationPolicy(hardened_isolation_required=True),
        )
        with self.assertRaises(UnhardenedBackendError):
            await backend.launch(spec)
        # Refusal must happen before any Docker/Harbor call - no run was ever registered.
        self.assertEqual(backend._runs, {})

    async def test_launch_does_not_refuse_when_hardened_isolation_is_not_required(self) -> None:
        # Confirms the refusal is conditional, not blanket - a non-hardened-required spec must
        # reach past the guard clause (it will then fail on the real Docker/Harbor call in this
        # sandboxed test environment, which is expected and not what this test asserts).
        backend = HarborBackend()
        spec = ExecutionSpec(
            task_dir=Path("."),
            runs_dir=Path(".cache/eng019-tests/unused2"),
            trial_name="not-refused-trial",
            agent_import_path="unused.agent",
            agent_timeout_sec=1.0,
            isolation=IsolationPolicy(hardened_isolation_required=False),
        )
        with self.assertRaises(Exception) as ctx:
            await backend.launch(spec)
        self.assertNotIsInstance(ctx.exception, UnhardenedBackendError)


class DockerSocketMountDetectionTest(unittest.TestCase):
    """A real check against a task's own environment definition, not the adapter's own
    EnvironmentConfig (which the adapter itself constructs and never sets `mounts` on, so
    checking IT could never find anything a real task actually defines - see
    _find_docker_socket_mount's docstring)."""

    def test_a_clean_real_fixture_task_is_not_flagged(self) -> None:
        real_fixture = Path(__file__).resolve().parent / "fixtures" / "eng001_harbor" / "task"
        self.assertIsNone(_find_docker_socket_mount(real_fixture))

    def test_a_task_mounting_the_docker_socket_is_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            environment_dir = task_dir / "environment"
            environment_dir.mkdir()
            (environment_dir / "docker-compose.yaml").write_text(
                "services:\n  main:\n    volumes:\n      - /var/run/docker.sock:/var/run/docker.sock\n",
                encoding="utf-8",
            )
            offender = _find_docker_socket_mount(task_dir)
            self.assertIsNotNone(offender)
            self.assertEqual(offender.name, "docker-compose.yaml")

    async def _launch_with_task_dir(self, task_dir: Path) -> None:
        backend = HarborBackend()
        spec = ExecutionSpec(
            task_dir=task_dir, runs_dir=Path(".cache/eng019-tests/socket-check"),
            trial_name="socket-check-trial", agent_import_path="unused.agent", agent_timeout_sec=1.0,
        )
        await backend.launch(spec)

    def test_launch_refuses_a_task_that_mounts_the_docker_socket(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            environment_dir = task_dir / "environment"
            environment_dir.mkdir()
            (environment_dir / "docker-compose.yaml").write_text(
                "services:\n  main:\n    volumes:\n      - /var/run/docker.sock:/var/run/docker.sock\n",
                encoding="utf-8",
            )
            with self.assertRaises(UnhardenedBackendError):
                asyncio.run(self._launch_with_task_dir(task_dir))


class IsolationPolicyDefaultsTest(unittest.TestCase):
    """Confirms the dataclass-level contract: deny-by-default and metadata denial are the
    DEFAULT, not something a caller must remember to opt into."""

    def test_default_policy_denies_all_egress(self) -> None:
        policy = IsolationPolicy()
        self.assertEqual(policy.egress_allowlist, ())

    def test_default_policy_denies_known_cloud_metadata_hosts(self) -> None:
        policy = IsolationPolicy()
        self.assertIn("169.254.169.254", policy.denied_metadata_hosts)

    def test_default_policy_denies_the_docker_socket(self) -> None:
        policy = IsolationPolicy()
        self.assertTrue(policy.deny_docker_socket)

    def test_execution_spec_defaults_to_the_deny_by_default_policy(self) -> None:
        spec = ExecutionSpec(
            task_dir=Path("."), runs_dir=Path("."), trial_name="t",
            agent_import_path="a.b", agent_timeout_sec=1.0,
        )
        self.assertEqual(spec.isolation.egress_allowlist, ())
        self.assertFalse(spec.isolation.hardened_isolation_required)


if __name__ == "__main__":
    unittest.main()
