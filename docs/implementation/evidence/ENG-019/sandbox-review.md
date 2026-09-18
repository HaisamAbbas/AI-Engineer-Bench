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

## Implemented

- `packages/aieb-runner/src/aieb_runner/backends/base.py`: `IsolationPolicy` (egress allowlist,
  deny-by-default default, cloud-metadata host denial, `deny_docker_socket`,
  `hardened_isolation_required`) added as a new field on `ExecutionSpec`. `UnhardenedBackendError`
  is the typed refusal a backend raises when asked for isolation it cannot provide.
- `packages/aieb-runner/src/aieb_runner/backends/egress_proxy.py`: a real, working deny-by-default
  HTTP/CONNECT forward proxy (`EgressGuardProxy`). Application-layer enforcement, disclosed as
  such: it works via `HTTP_PROXY`/`HTTPS_PROXY` environment variables, so a normal HTTP client
  honoring them is denied and logged for any non-allowlisted host, and for the cloud-metadata
  host unconditionally (SE-02) even if a policy mistakenly allowlists it. A raw socket that
  ignores those environment variables bypasses it - the same disclosed limitation the existing
  "Harbor public-egress/metadata adversarial validation | Deferred" row already names.
- `packages/aieb-runner/src/aieb_runner/backends/harbor/backend.py`: `HarborBackend.launch()`
  now (1) refuses outright (raises `UnhardenedBackendError`, before any Docker/Harbor call) when
  `hardened_isolation_required=True` - a non-hardened backend cannot be used as if it were one,
  not merely documented as not being one; (2) wires an `EgressGuardProxy` into the launched
  container's environment for whatever it can enforce; (3) asserts no Docker-socket mount is
  ever configured; (4) closes the guard proxy on cleanup.
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
for this - the existing one already reproduces exactly the guarantee asked for.

## Verified results (actual, measured)

- `tests/test_eng019_sandbox_threat_model.py`: **11/11 passed** (0.7s). Covers: deny-by-default
  denies an unlisted host and logs it; an empty allowlist denies everything; SE-02 (cloud-metadata
  host denied and logged even if allowlisted); an allowlisted host is genuinely forwarded (not
  merely never denied - proven against a real local HTTP server); `env_vars()` advertises the
  container-reachable host, not the bind host; a hardened-isolation-required launch is refused
  before any Docker/Harbor call; a non-hardened-required launch is NOT refused (the guard is
  conditional, not blanket); `IsolationPolicy`/`ExecutionSpec` defaults are deny-by-default.
- SE-01 (escaping symlink -> artifact rejected): cited, not duplicated -
  `tests/test_candidate_artifacts.py::CandidateArtifactsTest::test_rejects_symlink_escape`
  (ENG-003), re-run and confirmed passing as part of the new `sandbox-integration.yml` workflow.
- Retention reference safety: cited above, re-run and confirmed passing.

## Remaining external acceptance (unchanged, disclosed)

- "Official VM provider" selection: Deferred (DECISIONS.md). No cloud budget/authorization
  exists in this environment.
- "Harbor public-egress/metadata adversarial validation": Deferred, unchanged by this pass - the
  egress guard here is real and tested, but it is an application-layer convention (proxy
  environment variables), not a network-namespace-level guarantee against a hostile binary that
  opens raw sockets.
- ADR-11's S3-compatible artifact storage migration: re-deferred, not started.
- Independent review of this closure and current-tree remote CI remain open, matching every
  other ticket's acceptance policy in this repository.
