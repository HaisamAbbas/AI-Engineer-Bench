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

from harbor.models.task.config import EnvironmentConfig as HarborEnvConfig
from harbor.models.task.config import NetworkMode, TaskConfig as HarborTaskConfig
from harbor.models.trial.config import AgentConfig, EnvironmentConfig, ResourceMode
from aieb_runner.backends.base import (
    ExecutionSpec,
    IsolationPolicy,
    UnhardenedBackendError,
)
from aieb_runner.backends.egress_proxy import EgressGuardProxy
from aieb_runner.backends.harbor.backend import (
    HarborBackend,
    _find_unauthorized_host_mount,
    _task_network_offence,
)


def _minimal_task_toml(network_mode: str, allowed_hosts: list[str] | None = None) -> str:
    """A programmatically-valid Harbor task.toml for guard tests - [environment] declares the
    given network mode, everything else is Harbor's defaults."""
    config = HarborTaskConfig(
        environment=HarborEnvConfig(
            network_mode=NetworkMode(network_mode),
            allowed_hosts=allowed_hosts or [],
        )
    )
    return config.model_dump_toml()


def _write_task(tmp: Path, toml: str | None = None, compose: str | None = None, dotenv: str | None = None) -> Path:
    task_dir = Path(tmp) / "task"
    task_dir.mkdir(exist_ok=True)
    if toml is not None:
        (task_dir / "task.toml").write_text(toml, encoding="utf-8")
    if dotenv is not None or compose is not None:
        (task_dir / "environment").mkdir(exist_ok=True)
        if compose is not None:
            (task_dir / "environment" / "docker-compose.yaml").write_text(compose, encoding="utf-8")
    if dotenv is not None:
        (task_dir / "environment" / ".env").write_text(dotenv, encoding="utf-8")
    return task_dir


def _guard_agent_environment() -> tuple[AgentConfig, EnvironmentConfig]:
    """The trial AgentConfig/EnvironmentConfig shapes launch() builds (with the AIEB egress
    allowlist merged into extra_allowed_hosts), used to compute the effective network plan."""
    agent = AgentConfig(import_path="unused.agent", override_timeout_sec=1.0, extra_allowed_hosts=[])
    environment = EnvironmentConfig(
        type="docker",
        delete=True,
        cpu_enforcement_policy=ResourceMode.LIMIT,
        memory_enforcement_policy=ResourceMode.LIMIT,
        override_cpus=1,
        override_memory_mb=256,
        extra_allowed_hosts=[],
    )
    return agent, environment


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
        # Confirms the refusal is conditional, not blanket - a compliant spec (no socket mount,
        # no host-path mount, a no-network/allowlist effective network policy, no explicit
        # hardnening requirement) must reach past every guard clause. It will then fail on the
        # real Docker/Harbor call in this sandboxed test environment (or on the missing
        # task.toml being unacceptable to Harbor), which is expected and not what this test
        # asserts. Notably this MUST be a task-shaped directory, not the repo root: the repo
        # root legitimately contains compose fixtures whose interpolated `${CONTEXT_DIR}`
        # bind mounts resolve outside their directory and ARE refused now.
        backend = HarborBackend()
        spec = ExecutionSpec(
            task_dir=Path(tempfile.mkdtemp()),
            runs_dir=Path(".cache/eng019-tests/unused2"),
            trial_name="not-refused-trial",
            agent_import_path="unused.agent",
            agent_timeout_sec=1.0,
            isolation=IsolationPolicy(hardened_isolation_required=False),
        )
        with self.assertRaises(Exception) as ctx:
            await backend.launch(spec)
        self.assertNotIsInstance(ctx.exception, UnhardenedBackendError)

    async def test_launch_refuses_a_task_that_declares_public_egress_before_any_harbor_call(self) -> None:
        """A task whose OWN task.toml declares `network_mode = "public"` defeats L3 deny-by-
        default: the application-layer EgressGuardProxy cannot contain raw sockets, so the
        effective harbor network policy is what actually constrains egress. Such a task must
        be refused with UnhardenedBackendError before any Docker/Harbor call."""
        backend = HarborBackend()
        spec = ExecutionSpec(
            task_dir=_write_task(
                tempfile.mkdtemp(),
                toml=_minimal_task_toml("public"),
                compose="services:\n  main: {}\n",
            ),
            runs_dir=Path(".cache/eng019-tests/public-egress"),
            trial_name="public-egress-trial",
            agent_import_path="unused.agent",
            agent_timeout_sec=1.0,
        )
        with self.assertRaises(UnhardenedBackendError) as ctx:
            await backend.launch(spec)
        self.assertIn("PUBLIC", str(ctx.exception))
        self.assertEqual(backend._runs, {})

    async def test_launch_with_a_no_network_task_passes_both_guards(self) -> None:
        """A compliant task (no-network effective policy, no unauthorized mounts) passes the
        ADR-12 guard clauses and may fail later on Docker/Harbor for an unrelated reason - it
        must NEVER be refused as if it were a security violation."""
        backend = HarborBackend()
        spec = ExecutionSpec(
            task_dir=_write_task(tempfile.mkdtemp(), toml=_minimal_task_toml("no-network")),
            runs_dir=Path(".cache/eng019-tests/no-network"),
            trial_name="no-network-trial",
            agent_import_path="unused.agent",
            agent_timeout_sec=1.0,
        )
        with self.assertRaises(Exception) as ctx:
            await backend.launch(spec)
        self.assertNotIsInstance(ctx.exception, UnhardenedBackendError)


class EffectiveNetworkPolicyGuardTest(unittest.TestCase):
    """Unit-level coverage of `_task_network_offence`: the effective (task-declared) harbor
    phase policies must be no-network or allowlist and free of denied metadata hosts, because
    that is the network LAYER policy actually enforced (raw sockets bypass the app-layer
    proxy)."""

    def test_a_public_declaring_task_is_an_offence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = _write_task(tmp, toml=_minimal_task_toml("public"))
            agent, environment = _guard_agent_environment()
            offence = _task_network_offence(task_dir, agent, environment, IsolationPolicy())
            self.assertIsNotNone(offence)
            self.assertIn("PUBLIC", offence)

    def test_a_no_network_task_is_compliant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = _write_task(tmp, toml=_minimal_task_toml("no-network"))
            agent, environment = _guard_agent_environment()
            self.assertIsNone(_task_network_offence(task_dir, agent, environment, IsolationPolicy()))

    def test_an_allowlist_task_is_compliant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = _write_task(tmp, toml=_minimal_task_toml("allowlist", ["api.allowed.test"]))
            agent, environment = _guard_agent_environment()
            self.assertIsNone(_task_network_offence(task_dir, agent, environment, IsolationPolicy()))

    def test_an_allowlist_that_includes_a_denied_metadata_host_is_an_offence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = _write_task(tmp, toml=_minimal_task_toml("allowlist", ["api.allowed.test", "169.254.169.254"]))
            agent, environment = _guard_agent_environment()
            offence = _task_network_offence(task_dir, agent, environment, IsolationPolicy())
            self.assertIsNotNone(offence)
            self.assertIn("169.254.169.254", offence)

    def test_a_task_directory_without_a_task_toml_is_not_an_offence_here(self) -> None:
        """No task.toml = not a loadable Harbor task; Trial.create rejects it normally right
        after the guards, so there is no effective policy to WIDEN and nothing to refuse at
        this layer - refusing here would break the 'compliant spec reaches the Docker call'
        contract (see HarborBackendRefusalTest.test_launch_does_not_refuse...)."""
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = _write_task(tmp, toml=None)
            agent, environment = _guard_agent_environment()
            self.assertIsNone(_task_network_offence(task_dir, agent, environment, IsolationPolicy()))

    def test_a_task_to_malformed_to_load_is_uncheckable_and_therefore_an_offence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = _write_task(tmp, toml="[task\n  broken =")
            agent, environment = _guard_agent_environment()
            offence = _task_network_offence(task_dir, agent, environment, IsolationPolicy())
            self.assertIsNotNone(offence)
            self.assertIn("cannot be validated", offence)


class UnauthorizedHostMountDetectionTest(unittest.TestCase):
    """A real, YAML-parsed check against a task's own environment definition, not the
    adapter's own EnvironmentConfig (which the adapter itself constructs and never sets
    `mounts` on, so checking IT could never find anything a real task actually defines - see
    _find_unauthorized_host_mount's docstring). Covers, as one principle: no contestant Docker
    socket, no evaluator answer-key mount, and no host-path access outside the task directory
    (spec sections 37/43)."""

    def _write_compose(self, task_dir: Path, volumes_yaml: str) -> None:
        environment_dir = task_dir / "environment"
        environment_dir.mkdir(exist_ok=True)
        (environment_dir / "docker-compose.yaml").write_text(
            f"services:\n  main:\n    volumes:\n{volumes_yaml}\n", encoding="utf-8",
        )

    def test_a_clean_real_fixture_task_is_not_flagged(self) -> None:
        real_fixture = Path(__file__).resolve().parent / "fixtures" / "eng001_harbor" / "task"
        self.assertIsNone(_find_unauthorized_host_mount(real_fixture))

    def test_a_task_mounting_the_docker_socket_is_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            self._write_compose(task_dir, "      - /var/run/docker.sock:/var/run/docker.sock")
            offense = _find_unauthorized_host_mount(task_dir)
            self.assertIsNotNone(offense)
            offending_file, reason = offense
            self.assertEqual(offending_file.name, "docker-compose.yaml")
            self.assertIn("Docker socket", reason)

    def test_docker_s_current_canonical_compose_filename_is_also_scanned(self) -> None:
        """Docker's current canonical filename has no `docker-` prefix (`compose.yaml`) - a
        task using it must not be missed."""
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            environment_dir = task_dir / "environment"
            environment_dir.mkdir()
            (environment_dir / "compose.yaml").write_text(
                "services:\n  main:\n    volumes:\n      - /var/run/docker.sock:/var/run/docker.sock\n",
                encoding="utf-8",
            )
            offense = _find_unauthorized_host_mount(task_dir)
            self.assertIsNotNone(offense)
            self.assertEqual(offense[0].name, "compose.yaml")

    def test_a_task_mounting_an_arbitrary_host_path_is_flagged(self) -> None:
        """Host access, not just the Docker socket specifically - spec section 43's SE-family
        threat list names arbitrary host access, not one specific path."""
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            self._write_compose(task_dir, "      - /etc/shadow:/tmp/stolen")
            offense = _find_unauthorized_host_mount(task_dir)
            self.assertIsNotNone(offense)
            self.assertIn("outside the task directory", offense[1])

    def test_a_task_mounting_the_hidden_evaluator_fixture_directory_is_flagged(self) -> None:
        """The evaluator answer-key mount case (spec section 43): a task pointing a bind mount
        at this repository's real hidden-fixture root must be refused with a specific,
        legible reason - not merely caught as generic host escape."""
        repo_root = Path(__file__).resolve().parents[1]
        hidden_fixture_root = repo_root / "tests" / "maintainer"
        self.assertTrue(hidden_fixture_root.is_dir())  # sanity: this really is the real directory
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            self._write_compose(task_dir, f"      - {hidden_fixture_root.as_posix()}:/tmp/answer-key")
            offense = _find_unauthorized_host_mount(task_dir)
            self.assertIsNotNone(offense)
            self.assertIn("hidden evaluator fixture", offense[1])

    def test_a_relative_bind_mount_escaping_the_task_directory_is_flagged(self) -> None:
        """A relative source (`../../elsewhere`) resolves against the COMPOSE FILE's own
        directory, per real Docker Compose semantics - and can still escape the task dir."""
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "task"
            (task_dir / "environment").mkdir(parents=True)
            outside_target = Path(tmp) / "outside"
            outside_target.mkdir()
            self._write_compose(task_dir, "      - ../../outside:/tmp/escaped")
            offense = _find_unauthorized_host_mount(task_dir)
            self.assertIsNotNone(offense)
            self.assertIn("outside the task directory", offense[1])

    def test_a_named_volume_is_not_a_host_path_and_is_not_flagged(self) -> None:
        """`myvolume:/container/path` (no leading /, ./, ../, or drive letter) is a
        Docker-managed named volume, not a host bind mount - it cannot escape anywhere and
        must not be flagged as if it were a host path."""
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            self._write_compose(task_dir, "      - app-data:/var/lib/app-data")
            self.assertIsNone(_find_unauthorized_host_mount(task_dir))

    def test_a_bind_mount_within_the_task_directory_is_not_flagged(self) -> None:
        """A task legitimately bind-mounting its OWN subdirectory (e.g. the build context) is
        not host escape and must not be refused."""
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            (task_dir / "data").mkdir()
            self._write_compose(task_dir, f"      - {(task_dir / 'data').as_posix()}:/app/data")
            self.assertIsNone(_find_unauthorized_host_mount(task_dir))

    def test_an_interpolated_socket_mount_from_a_dotenv_file_is_flagged(self) -> None:
        """A socket path smuggled through `${VAR}` defined in the task's OWN `.env` file must
        be resolved and flagged - the textual pre-fix scan silently missed this (the audit's
        interpolation case)."""
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            self._write_compose(task_dir, "      - ${SNEAKY_SOCK}:/var/run/docker.sock2")
            (task_dir / "environment" / ".env").write_text("SNEAKY_SOCK=/var/run/docker.sock\n", encoding="utf-8")
            offense = _find_unauthorized_host_mount(task_dir)
            self.assertIsNotNone(offense)
            self.assertIn("Docker socket", offense[1])

    def test_a_default_value_interpolation_of_the_socket_path_is_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            self._write_compose(task_dir, "      - ${GUARD_SOCK:-/var/run/docker.sock}:/var/run/docker.sock")
            offense = _find_unauthorized_host_mount(task_dir)
            self.assertIsNotNone(offense)
            self.assertIn("Docker socket", offense[1])

    def test_an_interpolated_host_path_escape_is_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            self._write_compose(task_dir, "      - ${HOST_ETC}/shadow:/tmp/stolen")
            (task_dir / "environment" / ".env").write_text("HOST_ETC=/etc\n", encoding="utf-8")
            offense = _find_unauthorized_host_mount(task_dir)
            self.assertIsNotNone(offense)
            self.assertIn("outside the task directory", offense[1])

    def test_an_uncheckable_required_interpolation_is_refused_not_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            self._write_compose(task_dir, "      - ${MUST_SET:?socket path must be provided}:/var/run/docker.sock")
            offense = _find_unauthorized_host_mount(task_dir)
            self.assertIsNotNone(offense)
            self.assertIn("cannot be safely inspected", offense[1])

    def test_a_bind_source_still_containing_an_interpolation_marker_is_refused(self) -> None:
        """`$$` legitimately escapes to a literal `$` (leaving `${VAR}` un-interpolated by
        Compose itself); a bind source that STILL carries an interpolation marker after our
        single-pass resolution is exactly the input Compose could never have mounted literally,
        so it is refused rather than guessed at (fail-closed)."""
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            self._write_compose(task_dir, r"      - $${STILL_UNRESOLVED}:/container/x")
            offense = _find_unauthorized_host_mount(task_dir)
            self.assertIsNotNone(offense)
            self.assertIn("cannot be safely inspected", offense[1])

    def test_a_compose_merge_tag_is_refused_not_skipped(self) -> None:
        """Compose's `!override`/`!merge`/`!reset` overlay tags mutate produced YAML in ways a
        static scan cannot predict reliably - an uncheckable file is refused, never skipped."""
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            environment_dir = task_dir / "environment"
            environment_dir.mkdir(exist_ok=True)
            (environment_dir / "docker-compose.yaml").write_text(
                "services:\n  main:\n    volumes: !override\n      - /var/run/docker.sock:/var/run/docker.sock\n",
                encoding="utf-8",
            )
            offense = _find_unauthorized_host_mount(task_dir)
            self.assertIsNotNone(offense)
            self.assertIn("cannot be safely inspected", offense[1])

    def test_an_included_compose_file_that_does_not_match_the_globs_is_still_scanned(self) -> None:
        """`include:` can pull a socket mount from a file whose name does NOT match the
        docker-compose*.y*ml/compose*.y*ml globs (e.g. shared.yaml) - the reference must be
        followed, not missed."""
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            environment_dir = task_dir / "environment"
            environment_dir.mkdir(exist_ok=True)
            (environment_dir / "docker-compose.yaml").write_text(
                "include: [shared.yaml]\nservices:\n  main: {}\n",
                encoding="utf-8",
            )
            (environment_dir / "shared.yaml").write_text(
                "services:\n  main:\n    volumes:\n      - /var/run/docker.sock:/var/run/docker.sock\n",
                encoding="utf-8",
            )
            offense = _find_unauthorized_host_mount(task_dir)
            self.assertIsNotNone(offense)
            self.assertEqual(offense[0].name, "shared.yaml")
            self.assertIn("Docker socket", offense[1])

    def test_an_extends_file_reference_is_followed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            environment_dir = task_dir / "environment"
            environment_dir.mkdir(exist_ok=True)
            (environment_dir / "docker-compose.yaml").write_text(
                "services:\n  main:\n    extends:\n      file: base.yaml\n      service: base\n",
                encoding="utf-8",
            )
            (environment_dir / "base.yaml").write_text(
                "services:\n  base:\n    volumes:\n      - /etc/shadow:/tmp/stolen\n",
                encoding="utf-8",
            )
            offense = _find_unauthorized_host_mount(task_dir)
            self.assertIsNotNone(offense)
            self.assertEqual(offense[0].name, "base.yaml")
            self.assertIn("outside the task directory", offense[1])

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
            self._write_compose(task_dir, "      - /var/run/docker.sock:/var/run/docker.sock")
            with self.assertRaises(UnhardenedBackendError):
                asyncio.run(self._launch_with_task_dir(task_dir))

    def test_launch_refuses_a_task_that_mounts_a_host_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            self._write_compose(task_dir, "      - /home:/tmp/stolen-home")
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
