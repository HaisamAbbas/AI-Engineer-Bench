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

## Second independent review round (2026-09-18) - two of the prompt's own three named threats were untested and invisible in this document

A further review, re-reading Prompt 15's own test-list clause by clause against the tree (not
just against prior findings), found the acceptance sentence quoted at the top of this document
- *"no host, secret, hidden-label, or other-trial access"* - was only ONE-THIRD actually
tested (metadata access only); host access and hidden-label access had no test at all, and
neither gap was listed under "Remaining external acceptance" - meaning a reader could
reasonably conclude the quoted sentence was fully covered by a green 16/16 test file. That is
exactly the class of problem this project's disclosure discipline exists to prevent. Separately,
"no contestant Docker socket **or evaluator answer-key mount**" was only half-enforced - the
socket half had a real check, the answer-key half had none.

Fixed: `_find_docker_socket_mount()` is replaced by a generalized, YAML-parsed
`_find_unauthorized_host_mount()` that treats Docker socket access, evaluator-answer-key
access, and general host-path access as ONE underlying principle - a task never legitimately
needs to bind-mount anything from outside its own directory, so ANY bind-mount source
resolving outside `task_dir` is refused, with the Docker socket and this repository's real
hidden-fixture root (`tests/maintainer/`) called out by name for a clearer error when they are
the specific offender. This closes host access and the evaluator-answer-key mount for real,
with real tests (`tests/test_eng019_sandbox_threat_model.py::UnauthorizedHostMountDetectionTest`,
9 tests): a clean real fixture is not flagged; the Docker socket is flagged (both compose-file
naming conventions); an arbitrary host path (`/etc/shadow`) is flagged; this repository's real
`tests/maintainer/` directory is flagged by name when mounted; a relative bind-mount source
that resolves outside the task directory via the compose file's own directory is flagged; a
named Docker volume (not a host path at all) is correctly NOT flagged; a bind mount within the
task's own directory is correctly NOT flagged; and `launch()` genuinely refuses both the
socket and a generic host-path case, not just the socket.

**Cross-trial access remains untested and is now explicitly disclosed as such** (moved to
"Remaining external acceptance" below), rather than left silently absent: a real adversarial
test (two concurrently running containers, one attempting to read or reach the other's
workspace/network) requires live multi-container Docker orchestration this pass did not build.
Docker Compose's own default behavior (each trial gets its own project-scoped network,
confirmed by this adapter's existing `cleanup()` naming convention - `f"{trial_name}__env"` is
literally the compose project name Harbor derives per trial) is a real, load-bearing property
that argues cross-trial network isolation likely already holds by construction - but "likely
holds by construction" is exactly the standard this same review round already rejected once
for the Docker-socket check, so it is disclosed as unverified, not asserted as tested.

Also cited (was previously built but never referenced from this document): deadline and
cleanup-failure coverage that Prompt 15's own test list names is not new - it already exists
and was re-run to confirm it still passes:
`tests/test_attempt_lifecycle.py::AttemptLifecycleTests::test_deadline_stops_process_tree_before_artifact_freeze`
(a deadline stops the process tree, including a detached late-writing child, before the
artifact freeze - the late write never appears in the frozen candidate),
`::test_cancel_event_stops_engineering_before_deadline_with_no_verdict` (cancellation
interrupts a still-running attempt before its deadline, never producing a verdict), and
`::test_configuration_and_teardown_failures_do_not_become_verdicts` (a configuration or
teardown failure is classified as infrastructure-invalid, never silently scored as a verdict).

## Third independent review round (2026-09-19/20) - two more of Prompt 15's named threats were still bypassable, both fixed fail-closed

A further review, again re-reading Prompt 15's test list against the TREE rather than against
the previous document, found two named threats the second round's fix left open:

1. **Compose interpolation could bypass the socket/host-path mount check entirely.** The
   second round's `_find_unauthorized_host_mount` scanned raw compose YAML text for
   `docker.sock`. A task could trivially write `mounts: ["- ${DOCKER_SOCK}/var/run/docker.sock"]`
   with `DOCKER_SOCK=.` in a sibling `.env` (or `$XDG_RUNTIME_DIR`, etc.) and the literal-text
   scan saw only a `${...}` placeholder - the host-path offence resolved only after Docker Compose
   itself interpolated it, i.e. exactly when the task's definition was actually used. The crate
   hierarchy (`.env` next to compose, compose next to `include:`/`extends:` targets) multiplied
   the bypass surface: a malicious `shared.yaml` pulled in via `include:` never matched the
   `docker-compose*.yaml` glob at all.

   Fixed fail-closed: the bind-scan now resolves `${VAR}`/`$VAR` and the `:-`/`-`/`:?`/`?`/`:+`/`+`
   operators against the compose file's sibling `.env` plus the parent process environment
   (process env wins, matching docker/compose semantics); refuses any bind source still
   containing `$` after resolution and any `${X:?required}` whose variable is unset; parses with
   `yaml.safe_load` so unknown tags (`!override`, `!merge`, `!reset`) and any unparsable file
   become an explicit refusal ("cannot be safely inspected") rather than a silent skip; and walks
   the compose dependency graph - `include:` entries and `extends: {file: ...}` targets - so a
   docker-socket mount hidden in a referenced `shared.yaml` is caught even though that file never
   matches the compose glob. Verified against a REAL specimen found in this repo's own research
   tree (`.cache/research/harbor-v0.22.0/.../clbench/task-template/environment/docker-compose.yaml`,
   which mounts `${CONTEXT_DIR}/messages` - previously skipped, now correctly flagged as outside
   the task directory).

2. **"Effective" network policy was never actually checked.** The guard verified only the
   *declared* allowlist on the in-memory `EnvironmentConfig` the same call had constructed. A
   task declaring no network mode at all falls through to Harbor's effective `PUBLIC` (its
   `allowlist.mode` default is `PUBLIC` when unset), and a task that allowlists the cloud-metadata
   egress host at the policy layer would have been launched with that host forwarded to a
   container. Prompt 15's `SE-02` sentence is about what IS dropped, not what a form says.

   Fixed fail-closed: before any Docker/Harbor call, `launch()` now computes the trial's
   EFFECTIVE plan with Harbor's own resolver (`resolve_trial_network_plan` plus
   `resolve_task_verifier_mode`/`resolve_step_verifier_mode` - the exact calls `Trial` itself
   uses, so there is no second, drift-prone copy of the merge logic) with the policy's
   `extra_allowed_hosts` merged into the agent AND environment configs, and refuses
   (`UnhardenedBackendError`) when the effective phase network is `PUBLIC` or when an ALLOWLIST
   phase includes a denied metadata host. A task dir with no `task.toml` is not an offence
   (nothing to widen; `Trial.create` rejects it anyway, and no backend call was made); a
   malformed `task.toml` is. A task declaring no network mode but relying on the default is
   therefore refused, not launched.

Both are now "offense -> refusal before any Docker/Harbor call", matching the same standard the
second round applied to the socket check.

## Fourth independent review round (codex, 2026-09-20) - scoped-credential hardening, six findings closed

The Prompt-15 closure pass's granted acceptance - per-attempt scoped credentials with candidate/
verifier identity separation - then received an independent codex review of the credential path.
It returned six findings; all six were closed with code and tests, in this repo, on this date. A
SECOND review round of the same credential path then found four of those closures incomplete and
re-opened them; that round is documented as the fifth review round immediately below, and the
outcome statements in the six items are qualified there rather than silently rewritten.

1. **CLEARED - the credential authorized no useful capability** (only introspection-style
   verify). New `GET /v1/attempts/{attempt_id}/candidate` is a credential-authorized capability:
   `Authorization: Bearer <token>` where the token must be a valid candidate- or verifier-role
   credential for EXACTLY that attempt; it returns the persisted candidate artifact metadata from
   `repository.load_stored_candidate` (404 when no candidate yet; 401 when no valid credential).
   Test: `test_get_candidate_capability_is_credential_authorized` (incl. cross-attempt 401 and
   post-revocation 401).
2. **CLEARED - subprocesses inherited the worker's full environment** (engineering used
   `{**os.environ, **extra_env}` at the old `lifecycle.py:577`; the BUILD and isolated VERIFY
   children inherited the parent env too). All three child channels now receive an explicit,
   allowlisted environment: `lifecycle.py` `_CHILD_ENV_ALLOWLIST` + `_sanitized_child_env()`
   (infra/build-tooling/ML-cache members only; `*_PASSWORD`/`*_SECRET*`/`*_TOKEN`/`*_API_KEY`/
   `AWS_*`/`AZURE_*`/`GCP_*` and the entire `AIEB_*` namespace excluded), applied in `_start`
   (always, not only when `extra_env` present) and via `os.environ.clear()`-then-update inside
   `_build_subprocess_entrypoint` and `_verify_subprocess_entrypoint` (snapshot BEFORE clear),
   with the issued attempt vars layered on. Regression: `test_subprocess_environ_never_inherits_worker_secrets`
   plants `AIEB_DATABASE_URL` + `CI_BUILD_TOKEN` sentinels in the worker env and asserts they
   never reach the engineering subprocess or the isolated VERIFY evaluator process, while PATH
   (allowlisted) and the delivered AIEB_* credential vars do.
3. **CLEARED - issuance/revocation were not lease-fenced and recovery never revoked.** Migration
   `bc5e9d4b2107` adds `work_item_id`/`worker_id`/`lease_generation` to `attempt_credential`;
   `issue_attempt_credential` is now fenced to the live leased work item under `FOR UPDATE`
   (worker/generation must match and the lease be unexpired or `LeaseFenceError` is raised and the
   row untouched - a stale worker can neither issue nor rotate, and can't revoke anything since
   revoking is only ever stricter); `revoke_attempt_credentials` (both roles) runs at the top of
   `reconcile_expired_leases`'s recovery loop inside the SAME locked single-commit transaction, so
   a crashed worker's tokens die at lease recovery instead of lingering up to their 1h TTL.
   `runner_bridge.py` threads the fence identity into both phase executors and handles
   `LeaseFenceError` like heartbeat fencing. Tests: `test_stale_worker_is_fenced_out_of_issuance`
   (takeover + expiration) and `test_reconcile_sweep_revokes_credentials_after_crash`.
4. **CLEARED - the PG suite errored on duplicate seeded identities.** `_seed_attempt` now derives
   uuid-based unique digests AND unique task/entrant slugs/versions per call; the two failing
   tests (`test_credentials_are_role_and_attempt_scoped`, `test_get_candidate_capability_...`)
   pass.
5. **CLEARED - invalid-token responses leaked expiry and bad UUIDs 500'd.** The verify endpoint
   returns `expires_at: None` on any failed validation; `_parse_attempt_or_404` turns malformed
   UUID path values into 404s. Tests: leak assertion in the endpoint test +
   `test_malformed_attempt_uuid_is_404_not_500`.
6. **CLEARED - stale docstring and whitespace.** `IsolationPolicy`'s docstring no longer claims
   scoped credentials are "deliberately unimplemented"; `git diff --check` is clean.

Verification against real PostgreSQL: `tests/test_attempt_credentials.py` now passes 10/10 against
the disposable `aieb-test-postgres` container (postgres:16 on `localhost:5544`, configured via
`postgresql+psycopg`), and `tests/test_attempt_lifecycle.py` passes 19/19 including the two
environment-delivery tests and the new scrub regression. Alembic has a single head
(`bc5e9d4b2107`) and the whole chain upgrades cleanly.

## Fifth independent review round (codex, 2026-09-20) - five incomplete closures re-opened and closed

A SECOND review round of the credential path found four of the fourth round's closures
incomplete, each with a concrete negative control that the then-current code failed - so the
fourth round's "closed" claims were premature. All four are now closed with code and tests. The
reviewer's negative controls are each reproduced as a regression test before the fix is
accepted; gap 3 was NOT claimed closed on the basis of this document's word alone - it is the
reviewer's own run of those controls that decides. That deciding run (2026-09-20, on `895814a`)
passed and accepted Gap 3 as closed; see the "Deciding review" note at the end of this round.

1. **Issuance was attempt- and role-blind (fourth round item 1/3 incomplete).** The fence
   checked the lease row's identity but NOT that the lease's work item belonged to the attempt
   being credentialed, and NEVER that the requested role matched the item's type - so an
   engineering lease on attempt A could mint a valid CANDIDATE credential for attempt B, and any
   item could mint either role. Closed: `issue_attempt_credential` now requires
   `WorkItemRow.attempt_id == attempt_id` in the fenced SELECT (cross-attempt refusal ->
   `LeaseFenceError`) and derives the allowed roles from the work-item TYPE via the
   `_WORK_ITEM_TYPE_ROLES` map (`engineering` -> `{candidate}`; `verification`/`regrade` ->
   `{verifier}`; anything else mints nothing; any other pairing ->
   `ValueError`). New controls: `test_issuance_is_fenced_to_the_attempt_of_the_lease` and
   `test_issuance_role_is_derived_from_work_item_type` (both green).
2. **A stale worker's delayed `finally`-revoke killed a replacement's rotated token (fourth
   round item 3 incomplete).** Revoke filtered only by attempt+role, so when a takeover rotated
   the credential under a new worker/generation, the OLD worker's late revoke no-oped nothing -
   it wiped the NEW token (reviewer controls `new_before_stale_revoke=True,
   new_after_stale_revoke=True`). Closed: `revoke_attempt_credential` is now FENCED by the
   issuing lease's stored fence identity - the UPDATE's WHERE includes `work_item_id`,
   `worker_id`, and `lease_generation` of the credential row, so the stale finally NO-OPS
   (returns False, changes nothing) and the rotated token survives. The reconciler's wholesale
   `revoke_attempt_credentials` (both roles, unconditional) stays reserved for lease recovery.
   New control: `test_stale_finally_revoke_noops_against_rotated_token`.
3. **The reconciler missed crashed regrade items (fourth round item 3 incomplete).** The
   recovery sweep revokes credentials only for `engineering`/`verification` items; a
   `regrade` item whose lease expired mid-verify (the worker crashed holding a verifier
   credential) was recovered without revoking it, so its token lived on to TTL. Closed:
   `reconcile_expired_leases` now selects `("engineering", "verification", "regrade")` and the
   regrade recovery branch revokes both roles at loop top exactly like verification. New
   control: `test_reconciler_revokes_credentials_after_crashed_regrade`.
4. **The candidate capability was not consumed, and was verifier-capable without a verifier
   (fourth round item 1/2 incomplete).** `GET /v1/attempts/{attempt_id}/candidate` was
   role-agnostic (a candidate-role token opened it) and returned only IDs/digests - a verifier
   who "got" the capability could not reconstruct what it was for, and the verification phase
   still read the candidate through the worker's bare DB session, so no code path ever
   exercised a credential-authorized read. Closed: the endpoint now returns the FULL
   `stored_candidate` payload and is VERIFIER-ROLE-ONLY, role-differentiated
   (`_require_role`: 401 absent/invalid, 403 valid-but-wrong-role); verification now ISSUES its
   verifier credential first, then reads the candidate through
   `repository.load_stored_candidate_authorized` - the SAME gate the endpoint enforces, raising
   `CredentialDeniedError` (-> `infrastructure_invalid`) when the token isn't a valid live
   verifier credential for that attempt - and revokes on every infra-abort path after issuance
   (`_abort_infra`) plus the phase-ending finally and the reconciler if the worker crashes.
   New controls: `test_get_candidate_capability_is_credential_authorized` (now asserts the full
   payload, cross-attempt 401, and same-attempt candidate-role 403).

A fifth finding in the same review was environmental, not credential-path: **the isolated VERIFY
child imported the evaluator module during spawn bootstrap, before the environment scrub, so
import-time module code observed worker secrets** (reviewer: "a module whose top-level captures
env sees what the var NAME filters alone cannot guarantee"). Closed: `_verify_subprocess_entrypoint`
now receives the evaluator as `(module, qualname)` identity STRINGS - never the pickled callable -
and resolves it via `_resolve_evaluator_by_identity` (importlib + attribute walk) ONLY after
`os.environ` is scrubbed; `_sanitized_child_env()` additionally strips embedded credentials from
allowlisted VALUES (URL userinfo like `http://user:pass@host`, `?password=/token=/key=/secret=`
query segments) so a name allowlist cannot smuggle a credential inside a legitimate-looking
variable. New controls: `test_import_time_env_leak_is_closed...` (a fixture evaluator whose
TOP-LEVEL code snapshots `os.environ` at import and asserts it never saw the planted secrets)
and `test_proxy_value_embedded_credentials_are_scrubbed_from_child_env`.

The reviewer's own run then re-opened that closure as a HIGH blocker: **the hosted worker still
imported the evaluator module in ITS OWN parent process before the isolated child existed.**
`runner_bridge.py` did `importlib.import_module(evaluator_module)` to build the callable it
passed into `run_verification`, so evaluator module-level code ran against the worker's full
unsanitized environment (`AIEB_DATABASE_URL`, CI tokens). The then-regression missed it only
because it imported the probe BEFORE planting secrets, so the probe's import-time snapshot was
never taken against the leak. Reproduced in a fresh process by the reviewer
(`parent_import_db=postgresql://sentinel:PLANTED@db.example/compromised`,
`parent_import_token=PLANTED_TOK`). Closed: evaluator identity now lives IN `TASK_RUNTIMES`
as a third element (source_dir, evaluator_module, qualname) and travels all the way from
`runner_bridge` to the spawn child as plain strings - `runner_bridge` no longer imports the
module at all, `run_verification` accepts `evaluate_identity=(module, qualname)` directly (a
`TypeError` guards the "exactly one of callable or identity" contract), and the isolated child is
the FIRST process to import the module, and only after the scrub. Negative controls that plant
the worker secrets BEFORE any probe import and then fail (asserted red, then reverted) against
the reintroduced parent import: rewritten
`test_import_time_env_leak_is_closed_evaluator_module_runs_after_scrub` (identity path,
`evaluator=None`, asserts the probe never enters the worker/test parent's `sys.modules`) and a
new end-to-end leased test `test_hosted_verification_never_imports_the_evaluator_in_the_worker_parent`
(real `execute_leased_work` engineering -> verification against the import-time probe,
asserting the probe stays out of the worker parent's `sys.modules` and the recorded evaluation's
import-time snapshot saw neither planted secret).

Verification (all green in this pass): `tests/test_attempt_credentials.py` 14/14,
`tests/test_attempt_lifecycle.py` 21/21 (incl. the two new controls above and the two new env
controls), `tests/test_worker_leasing.py` 39/39 (incl. the new leased parent-import control),
`tests/test_eng019_sandbox_threat_model.py` 39/39. Single alembic head `bc5e9d4b2107`, upgrade
chain green. Full mechanical detail in `operations-review.md` (fifth review round) and
DECISIONS.md ENG019-006/ENG020-007.

### Deciding review (codex, 2026-09-20) - Gap 3 accepted as closed on `895814a`

The reviewer ran the deciding negative-control pass against `895814a` (the commit that removed
the worker-parent evaluator import): the hosted worker imports NO evaluator modules in its parent
process; evaluator identity is plain `(module, qualname)` strings through
`runner_bridge.py` (`TASK_RUNTIMES` third element); the isolated VERIFY child is the ONLY
importer and imports only after `os.environ` is scrubbed (`lifecycle.py`
`_verify_subprocess_entrypoint`); the production-path parent-import regression passes; and
cross-attempt issuance, role-mismatch refusal, stale revocation, crashed-regrade revocation,
verifier-only candidate access, malformed-UUID handling, and invalid-token non-disclosure all
pass. Independent results: credential + worker-leasing 53/53, lifecycle + sandbox 60/60, `HEAD` =
`origin/main` = `895814a`, working tree clean, commit whitespace check clean. Verdict: **ENG-019
Gap 3 - scoped credentials and candidate/verifier identity separation - is accepted as closed for
the currently supported architecture.** This does not close ENG-019/ENG-020 overall: gaps 4
(restore-drill fencing), 5 (kill-switch API/CLI), 6 (Prometheus metrics/alerts), and the
explicitly deferred official VM/live-infrastructure gates remain open.

## Implementation - network-policy guard and hardened mount scan

- `packages/aieb-runner/src/aieb_runner/backends/harbor/backend.py`:
  - `_task_network_offence(task_dir) -> str | None`: reads the task's OWN `task.toml`
    (Harbor `TaskConfig`) and resolves its effective network plan via Harbor's own resolver;
    returns a refusal reason when the effective phase mode is `PUBLIC` (default included) or an
    ALLOWLIST phase contains a denied metadata host. Returns `None` (no offence) when there is no
    `task.toml`; returns an offence when it is malformed. The review's per-trial merge of
    `extra_allowed_hosts` into agent + environment configs is applied before resolution, so the
    refusal is measured against what the trial would ACTUALLY launch, matching
    `Trial._network_plan`'s own merge.
  - `_find_unauthorized_host_mount(task_dir) -> str | None` is now fully fail-closed: compose
    sources are discovered via the flat `{task_dir}/environment/*.yaml` delivery plus
    `include:`/`extends:` graph walking of every discovered file; each is parsed with
    `yaml.safe_load` (any parse/unknown-tag failure -> "cannot be safely inspected" offence);
    `${...}`/`$VAR` interpolation is resolved against sibling `.env` (docker/compose operator set)
    then the process environment; required-but-unset and residual-`$` bind sources refused; every
    bind source must resolve within `task_dir`. ASN.1 diagnostics name the socket, this repo's
    `tests/maintainer/` answer-key root, and generic host paths by their actual handler.
  - `launch()` ordering preserved: socket/host-path mount refusal precedes the network-policy
    refusal, so prior tests expecting the mount refusal still hold.

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
  launched container's environment; (3) refuses launch if the task's own environment definition
  mounts the Docker socket, the hidden evaluator fixture directory, or any other host path
  outside the task's own directory - and, since the third review round, that refusal RESISTS the
  sinkhole moves that emptied the first two rounds' checks: compose interpolation
  (`${VAR}`/`$VAR`/operators, `.env` + process env), `include:`/`extends:` graph walking,
  YAML-unknown-tag and unparsable-file refusals, and required-but-unset/residual-`$` sources
  (see the third review round below); (4) refuses launch when the task's EFFECTIVE network plan
  (Harbor's own resolver, post-`extra_allowed_hosts` merge - `_task_network_offence`) is
  `PUBLIC` or allowlists a denied metadata host, closing the "declared-vs-effective" hole;
  (5) closes the guard proxy on cleanup.
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

- `tests/test_eng019_sandbox_threat_model.py`: **39/39 passed** (up from 23 across the third
  review round). The prior 23 remain green; the third round adds `EffectiveNetworkPolicyGuardTest`
  (6 tests: a task declaring effective `PUBLIC` egress - including the undeclared default - is
  refused before any Harbor call; a `no-network` task passes both guards; a compliant allowlist
  passes; an allowlist naming a denied metadata host is refused; a task dir without `task.toml`
  is not refused; a malformed `task.toml` is refused) and 10 new mount-scan tests (sibling-`.env`
  docker-socket interpolation, `:-` default interpolation, `/etc/shadow` interpolation,
  required-interpolation refusal, residual-`$` refusal via `$${...}`, `!override` merge-tag
  refusal, `include: [shared.yaml]` graph following, `extends: {file: base.yaml}` graph following,
  plus the re-based UUID/clean-task guards). Because the repo ROOT itself now contains a real
  interpolated escaping mount under `.cache/research/`, the pre-refactor
  `test_launch_does_not_refuse_when_hardened_isolation_is_not_required` was re-based onto a bare
  temp dir (the guard is intentionally no longer satisfiable against this worktree's own root).
  Covers: deny-by-default denies an unlisted host and
  logs it; an empty allowlist denies everything; SE-02 (cloud-metadata host denied and logged
  even if allowlisted); an allowlisted host is genuinely forwarded (proven against a real local
  HTTP server); an unauthenticated request is refused with 407 before any policy check; the
  embedded per-instance token authenticates a real request; `env_vars()` advertises the
  container-reachable host, not the bind host; a hardened-isolation-required launch is refused
  before any Docker/Harbor call; a non-hardened-required launch is NOT refused (the guard is
  conditional, not blanket); a task mounting the Docker socket, an arbitrary host path, or this
  repository's real hidden-fixture directory is detected (both compose-file naming
  conventions) and its launch refused; a named Docker volume and an in-directory bind mount are
  correctly NOT flagged; a clean real fixture task is not flagged;
  `IsolationPolicy`/`ExecutionSpec` defaults are deny-by-default.
- SE-01 (escaping symlink -> artifact rejected): cited, not duplicated -
  `tests/test_candidate_artifacts.py::CandidateArtifactsTest::test_rejects_symlink_escape`
  (ENG-003), re-run and confirmed passing as part of the new `sandbox-integration.yml` workflow.
- Retention reference safety: cited above, re-run and confirmed passing.
- Deadline and cleanup-failure coverage (Prompt 15's own test list): cited above, all three
  re-run and confirmed passing (`tests/test_attempt_lifecycle.py`, 3 tests).

## Remaining external acceptance (unchanged or newly disclosed)

- "Official VM provider" selection: Deferred (DECISIONS.md). No cloud budget/authorization
  exists in this environment.
- "Harbor public-egress/metadata adversarial validation": Deferred, unchanged by this pass - the
  egress guard here is real, tested, and now authenticated, but it is still an application-layer
  convention (proxy environment variables), not a network-namespace-level guarantee against a
  hostile binary that opens raw sockets and ignores them entirely.
- **Cross-trial access: not tested.** A real adversarial two-container test (one trial writes,
  a concurrently running different trial attempts to read it, or reach it over the network)
  was not built - it requires live multi-container Docker orchestration beyond what this pass
  built. Docker Compose's default per-project network isolation (confirmed structurally via
  this adapter's own project-naming convention) argues this likely already holds, but "likely
  holds by construction" is not treated as equivalent to tested, matching the standard this
  same review applied to the Docker-socket check.
- **Per-attempt short-lived credentials and candidate/verifier identity separation (spec section
  37): IMPLEMENTED and, as of the fourth review round, PG-verified.** The `attempt_credential`
  table (one live credential per `(attempt_id, actor_role)`, sha256-hashed tokens, expiry +
  revocation, lease-fence identity per the fourth round), repository
  issue/verify/revoke/status (issue lease-fenced to the live work-item lease), a
  `POST /v1/attempts/{attempt_id}/credentials/verify` endpoint authenticated BY the presented
  credential itself (deliberately not operator-authenticated - that separation is the point) plus
  the credential-authorized `GET /v1/attempts/{attempt_id}/candidate` capability, and end-to-end
  delivery of `AIEB_ATTEMPT_ID`/`AIEB_ATTEMPT_ROLE`/`AIEB_ATTEMPT_CREDENTIAL` into allowlist-scrubbed
  engineering, BUILD, and isolated VERIFY subprocess environments
  (`EngineeringCommand.extra_env` / `run_verification(attempt_vars=...)`) are all implemented.
  `tests/test_attempt_credentials.py` passes 10/10 against the disposable `aieb-test-postgres`
  container (including the six-finding hardening suite), and `tests/test_attempt_lifecycle.py`
  passes 19/19 (env delivery + environment-scrub regression). The original inert
  `scoped_credential_id` field on `IsolationPolicy` is gone; the mechanism exists, is wired end to
  end through repository -> worker -> subprocess environment -> API capability, is fenced to the
  lease that issued it, and is revoked on phase end AND on lease recovery.
- ADR-11's S3-compatible artifact storage migration: re-deferred, not started.
- The two-references-one-blob retention case: holds by construction, not directly tested.
- Independent review of this closure and current-tree remote CI remain open, matching every
  other ticket's acceptance policy in this repository.
