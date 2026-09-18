# ENG-019 — Official sandbox and threat-model review

Date: 2026-09-18. Branch: `prompt-14-eng017-eng018` (continuing Prompt 15 on the same
worktree). Boundary decision: `ADR-12` in `DECISIONS.md`.

## Scope (deliberately narrower than "official hardened isolation")

STATUS.md's ENG-019 acceptance is specifically: *"SE-02 denies and logs verifier/cloud-metadata
access; no host, secret, hidden-label, or other-trial access; teardown and egress controls
pass."* This pass closes exactly that, at the container/application level, against the existing
`HarborBackend` (Docker). It does **not** claim VM-equivalent hardened isolation - "Official VM
provider" and "Harbor public-egress/metadata adversarial validation" remain listed as Deferred
in DECISIONS.md's Open decisions table, unchanged by this work.

## Independent review round (2026-09-18) - two real findings fixed, one field removed

Two independent reviews of the first pass found real gaps, addressed here:

1. **The Docker-socket check was vacuous.** The first pass inspected
   `config.environment.mounts` on the `EnvironmentConfig` object the SAME function had just
   constructed eight lines earlier, which never sets `mounts` - the check could never fire
   regardless of what any real task defined, and the first version of this document incorrectly
   described it as an active assertion. Fixed for real: `_find_docker_socket_mount()` scans the
   TASK's own environment definition (`task_dir/environment/docker-compose*.yaml` - the actual
   file Harbor's Docker environment builds from, confirmed against the real ENG-001 fixture at
   `tests/fixtures/eng001_harbor/task/environment/docker-compose.yaml`) for a literal
   `docker.sock` reference, and `launch()` refuses if found. Tested directly: a clean real
   fixture task is not flagged; a synthetic task whose compose file mounts the socket IS
   flagged and its launch IS refused
   (`tests/test_eng019_sandbox_threat_model.py::DockerSocketMountDetectionTest`, 3 tests).
2. **The guard proxy had no authentication.** `EgressGuardProxy` binds `0.0.0.0` (required so a
   container can reach the host at all via `host.docker.internal`) - for the lifetime of every
   trial that meant an unauthenticated forward proxy reachable from any machine on the same
   network, able to relay through it to allowlisted destinations including the model broker.
   Fixed: a random per-instance token is now required as HTTP Basic `Proxy-Authorization` on
   every request (checked with `hmac.compare_digest`, before any target host is even parsed);
   the token is embedded in the `HTTP_PROXY`/`HTTPS_PROXY` URLs' `user:pass@host` convention, so
   a normal HTTP client that already honors those environment variables sends it automatically
   with no candidate-side code change. Tested: an unauthenticated request is refused with 407
   before any policy check, and is not logged as a policy denial; the embedded token
   authenticates a real request end to end.
3. **`scoped_credential_id` was declared and never wired.** `IsolationPolicy` had an inert field
   for per-attempt scoped credentials - nothing in the repository issued, injected, scoped, or
   revoked one, and unlike every other limitation in this document it wasn't listed as deferred
   either, so it read as a capability that didn't exist. Removed rather than left inert or
   fabricated: no real cloud credential-issuance system exists in this environment to wire it
   against. Per-attempt short-lived credentials and real candidate/verifier identity separation
   (spec section 37) are now explicitly listed under "Remaining external acceptance" below.

A separate, unrelated bug surfaced by the same review round - `repository.cancel_campaign`'s
WHERE clause excluded `paused`, so `activate_kill_switch`'s "every non-terminal campaign"
teardown request silently no-opped for a paused campaign - is fixed and tested; recorded in
`docs/implementation/evidence/ENG-020/operations-review.md` since it is a kill-switch/ENG-020
concern, not this ticket's.

## Implemented

- `packages/aieb-runner/src/aieb_runner/backends/base.py`: `IsolationPolicy` (egress allowlist,
  deny-by-default default, cloud-metadata host denial, `deny_docker_socket`,
  `hardened_isolation_required`) added as a new field on `ExecutionSpec`. `UnhardenedBackendError`
  is the typed refusal a backend raises when asked for isolation it cannot provide.
- `packages/aieb-runner/src/aieb_runner/backends/egress_proxy.py`: a real, working deny-by-default,
  token-authenticated HTTP/CONNECT forward proxy (`EgressGuardProxy`). Application-layer
  enforcement, disclosed as such: it works via `HTTP_PROXY`/`HTTPS_PROXY` environment variables,
  so a normal HTTP client honoring them is denied and logged for any non-allowlisted host, and
  for the cloud-metadata host unconditionally (SE-02) even if a policy mistakenly allowlists it.
  A raw socket that ignores those environment variables bypasses the ALLOWLIST enforcement
  (disclosed limitation, unchanged) - but cannot reach an allowlisted destination through this
  proxy at all without the per-instance token, closing the network-exposure gap the review
  found.
- `packages/aieb-runner/src/aieb_runner/backends/harbor/backend.py`: `HarborBackend.launch()`
  now (1) refuses outright (raises `UnhardenedBackendError`, before any Docker/Harbor call) when
  `hardened_isolation_required=True`; (2) wires a token-authenticated `EgressGuardProxy` into the
  launched container's environment; (3) refuses launch if the task's own
  `environment/docker-compose*.yaml` mounts the Docker socket (real check, see above); (4) closes
  the guard proxy on cleanup.
- ADR-11's owed S3-compatible artifact storage migration is explicitly **re-deferred**, not
  silently absorbed into this pass or dropped - it remains its own ledgered follow-up.

## Retention reference safety (cited, not rebuilt)

Prompt 15 names retention-reference safety in its test list. Direct coverage already exists and
was verified to still pass: `tests/test_worker_leasing.py::WorkerLeasingTests::
test_staging_blobs_expire_after_24h_but_committed_evidence_never_purged` proves a blob with a
remaining live (candidate-claimed) reference survives the 24-hour orphan purge -
`attach_candidate_references` flips a blob's `retention_class` from `staging` to `evidence` the
moment any reference on it is claimed, and `purge_expired_worker_artifacts` only ever considers
`staging`-class blobs (`services/api/src/aieb_api/worker/repository.py`). No new test was added
for this - the existing one already reproduces exactly the guarantee asked for. Not yet covered:
the specific two-references-one-blob case (a shared blob with one expired staging reference and
one live one) - the invariant holds by construction today (a blob is `evidence`-class the moment
ANY reference on it is claimed, so a still-`staging` blob can never have a claimed reference),
but a direct test of the shared-blob case would keep it holding; flagged as a follow-up, not
added here.

## Verified results (actual, measured)

- `tests/test_eng019_sandbox_threat_model.py`: **16/16 passed** (~7s, up from 11 after this
  review round's fixes). Covers: deny-by-default denies an unlisted host and logs it; an empty
  allowlist denies everything; SE-02 (cloud-metadata host denied and logged even if
  allowlisted); an allowlisted host is genuinely forwarded (proven against a real local HTTP
  server); an unauthenticated request is refused with 407 before any policy check; the embedded
  per-instance token authenticates a real request; `env_vars()` advertises the
  container-reachable host, not the bind host; a hardened-isolation-required launch is refused
  before any Docker/Harbor call; a non-hardened-required launch is NOT refused (the guard is
  conditional, not blanket); a task mounting the Docker socket in its own environment
  definition is detected and its launch refused; a clean real fixture task is not flagged;
  `IsolationPolicy`/`ExecutionSpec` defaults are deny-by-default.
- SE-01 (escaping symlink -> artifact rejected): cited, not duplicated -
  `tests/test_candidate_artifacts.py::CandidateArtifactsTest::test_rejects_symlink_escape`
  (ENG-003), re-run and confirmed passing as part of the new `sandbox-integration.yml` workflow.
- Retention reference safety: cited above, re-run and confirmed passing.

## Remaining external acceptance (unchanged or newly disclosed)

- "Official VM provider" selection: Deferred (DECISIONS.md). No cloud budget/authorization
  exists in this environment.
- "Harbor public-egress/metadata adversarial validation": Deferred, unchanged by this pass - the
  egress guard here is real, tested, and now authenticated, but it is still an application-layer
  convention (proxy environment variables), not a network-namespace-level guarantee against a
  hostile binary that opens raw sockets and ignores them entirely.
- **Per-attempt short-lived credentials and candidate/verifier identity separation (spec section
  37): not implemented.** `scoped_credential_id` was removed from `IsolationPolicy` rather than
  left as an inert field - no real cloud credential-issuance system exists in this environment
  to issue, scope, or revoke one against. This is a genuine gap, not merely a documentation one.
- ADR-11's S3-compatible artifact storage migration: re-deferred, not started.
- The two-references-one-blob retention case: holds by construction, not directly tested.
- Independent review of this closure and current-tree remote CI remain open, matching every
  other ticket's acceptance policy in this repository.
