# ENG-020 — Staging/production CI/CD, backups, and runbooks

Date: 2026-09-18. No real cloud budget/authorization exists in this environment (same
constraint ENG-001/ENG-019 disclose). This closes what is genuinely implementable and testable
without provisioning paid infrastructure or deploying anything public, and explicitly names
what stays blocked.

## Third independent review round (2026-09-18) - two silently-dropped clauses closed, one gap disclosed instead of introduced late

A further review, re-reading Prompt 15's own text clause by clause against the tree, found two
requirements this document previously never mentioned at all - not fixed, not disclosed,
simply absent, which is worse than a disclosed gap:

1. **"Keep active campaign toolchains pinned across software upgrades" had no test anywhere.**
   It was in this project's own plan ("prove a frozen campaign's manifest doesn't shift under a
   simulated software upgrade") but never built. Closed for real:
   `tests/test_api_service.py::test_frozen_campaign_toolchain_stays_pinned_across_a_simulated_software_upgrade`
   freezes a real campaign, THEN registers a genuinely newer entrant revision (same slug,
   `agent_version` "1.0.0" -> "2.0.0", simulating a real deploy of an updated agent), and
   proves the already-frozen campaign's `resolved` manifest is completely unaffected - both at
   the raw database row and through a fresh `GET /v1/campaigns/{id}`. This is the existing
   ENG-002/ENG-014 frozen-manifest design (a snapshot taken at freeze time, not a live
   reference); the gap was the missing test, not missing machinery.
2. **"Exact staging validation steps" were never written**, even though Prompt 15 requires
   them specifically because cloud is unavailable ("If unavailable, produce complete
   infrastructure code, local tests, exact staging validation steps and explicitly blocked
   official gates"). `deployment-topology.md` covered components/credentials/environments/
   rollback/DR targets - all descriptive. Added `staging-validation-steps.md`: the ordered
   procedure (deploy, migrate, verify, smoke, rollback) an operator executes against a real
   staging environment the day cloud authorization exists - the deliverable that converts
   "blocked" into "ready to run when unblocked," citing the exact commands/scripts already
   built and locally verified in this repository rather than inventing new ones.

Also: Python lint/type-checking CI was claimed covered by a workflow comment
("lint/type/unit are covered by the existing per-area workflows' own unittest invocations")
that conflated unit testing with linting/type-checking - neither exists for Python anywhere in
this repository (no ruff/mypy/pyright config). `uv tool run ruff check .` was tried while
fixing this and found 400+ pre-existing findings across the whole codebase, unrelated to
Prompt 15's own scope. Introducing a new lint gate this late would either fail immediately on
that pre-existing surface or require touching many unrelated files ("implement only the
requested phase" - this project's own working rule) - so the misleading comment is corrected
and the gap disclosed in `release-candidate.yml` directly, rather than either overclaiming or
scope-creeping into an unrelated cleanup. TypeScript IS type-checked (the website's build/type
checks already run in `eng015-verification.yml`) - the gap is Python-only.

## Independent review round (2026-09-18) - one confirmed bug fixed, one test restructured

- **The kill switch silently skipped paused campaigns.** `activate_kill_switch` selects
  campaigns in `('frozen', 'running', 'paused')` and calls `repository.cancel_campaign` on
  each - but `cancel_campaign`'s own WHERE clause only matched `('frozen', 'running')`, so a
  call against a paused campaign returned rowcount 0 and silently did nothing, contradicting
  the function's own "every non-terminal campaign" docstring. A paused campaign is exactly the
  case that matters most here: it already has outstanding leased work and nothing else is
  stopping it. Fixed: `cancel_campaign` now includes `'paused'`. New regression:
  `tests/test_worker_leasing.py::test_kill_switch_tears_down_a_paused_campaign_too` claims work
  on a campaign, pauses it, activates the kill switch, and asserts the campaign actually
  transitions to `cancelling` (previously it silently stayed `paused`).
- **The backup/restore drill accepted a stale finalize before reconciliation, then rewound the
  database to keep the later assertions clean.** That proved less than it looked like: a real
  restore procedure must keep a pre-restore worker fenced throughout, not merely happen to
  reject it once something else later changes the generation. Restructured to drop the
  speculative pre-reconciliation attempt entirely - the drill now goes straight from "lease
  survived restore as still-leased" to reconciliation, then proves fencing against
  reconciliation's own output. All three assertions still pass, now without the confusing
  attempt-then-rewind pattern.
- The dependency-review job was on `release-candidate.yml`, gated on `if: github.event_name ==
  'pull_request'` - but that workflow only triggers on tag push and manual dispatch, so the job
  could never run. Moved to a new, dedicated `dependency-review.yml` triggered on `pull_request`
  for dependency-file changes, which is the only event type `dependency-review-action` can
  meaningfully run against (it compares base and head refs).
- `scoped_credential_id` was removed from `IsolationPolicy` (ENG-019) as dead/unwired, then the
  mechanism it promised - per-attempt scoped credentials with real candidate/verifier identity
  separation - was actually IMPLEMENTED in the Prompt-15 continuation pass (spec section 37; see
  "Scoped attempt credentials" below). The auto-pause threshold here is scoped per-campaign,
  not per-backend as spec section 39 literally states ("three consecutive failures... from the
  same backend") - defensible today since exactly one backend (`HarborBackend`) exists, but
  recorded here as a disclosed deviation rather than left silent, per review.

## Implemented

### Worker draining (spec section 40)
`services/api/src/aieb_api/worker/loop.py::install_drain_handlers` wires SIGTERM/SIGINT to the
`stop_event` that `run_worker` already checked at the top of every iteration (before claiming) -
that check existed; nothing reached it. A deployed worker previously had no way to receive a
drain signal at all, so an orchestrator's SIGTERM killed it mid-attempt and orphaned the lease -
exactly the failure this closes. Deliberately distinct from `cancel_event` (campaign
cancellation, which interrupts in-flight work): draining lets the current item finish and
finalize, then stops claiming new ones.

### Auto-pause and the global kill switch - two distinct mechanisms (spec sections 39/48)
- **Auto-pause** (`CampaignRow.auto_paused`/`consecutive_infrastructure_failures`, migration
  `d3f6a8e21c94`): per-campaign. Three consecutive VERIFICATION-or-regrade-phase infrastructure
  failures pause a running campaign automatically; any non-infrastructure outcome resets the
  counter to zero. Scoped to `verification`/`regrade` work-item types only, deliberately: an
  engineering-only `ExecutionResult.execution_validity` reports the same
  `"infrastructure_invalid"` value for a completely healthy phase that produced a candidate and
  advanced to verification (that phase never itself produces a scored verdict) - counting it
  would auto-pause on ordinary, successful engineering activity, reproduced directly while
  building this feature and fixed by gating on `leased.work_type` in `loop.py`, not on the
  result value alone. Resuming an auto-paused campaign requires
  `POST /v1/campaigns/{id}/resume {"acknowledge_auto_pause": true}` - a plain resume (as for a
  manual pause) is refused with 403, so the operator review the mechanism exists for cannot be
  skipped by habit.
- **Kill switch** (`kill_switch` singleton table, same migration): global, independent of any
   one campaign's health. `repository.activate_kill_switch` stops ALL new dispatch immediately
   (`claim_work_item` refuses unconditionally while active) and requests bounded teardown of
   active work by cancelling every non-terminal campaign through the existing, already-tested
   cancellation/drain machinery - not a second, novel teardown path.

### Scoped attempt credentials (spec section 37)
Implemented in the Prompt-15 continuation pass (the same codex audit's third gap) - NOT the
class of claim the earlier `scoped_credential_id` field made. Structure:
- `attempt_credential` table (migration `ba47e9c84511`): one live credential per
  `(attempt_id, actor_role)` (`candidate`|`verifier`), storing only a sha256 hash (a DB leak
  cannot mint usable tokens), with `expires_at` and `revoked_at`. Re-issuing rotates the token in
  place (unique constraint) rather than accumulating rows.
- Repository (`worker/repository.py`): `issue_attempt_credential` (returns the plaintext token
  exactly once, records a `credential.<role>.issued` attempt event),
  `verify_attempt_credential` (constant-time compare against the exact attempt+role row; no row /
  revoked / expired / wrong token all return false, never leaking why),
  `revoke_attempt_credential` (idempotent; the phase-end path), `attempt_credential_status`.
- Delivery end to end: the worker issues the candidate credential and injects
  `AIEB_ATTEMPT_ID`/`AIEB_ATTEMPT_ROLE`/`AIEB_ATTEMPT_CREDENTIAL` into the engineering
  subprocess environment (`EngineeringCommand.extra_env`), and issues the verifier credential
  delivered as `attempt_vars` into the spawn-based isolated VERIFY subprocess
  (`run_verification(attempt_vars=...)`); both are revoked in the phase executor's `finally`.
- The single control-plane presentation point, `POST /v1/attempts/{attempt_id}/credentials/verify`,
  is authenticated BY the presented credential itself - deliberately NOT operator-authenticated.
  A leaked verifier token proves exactly verifier-for-that-attempt and nothing else; a candidate
  credential proves candidate-for-that-attempt only. This is the concrete identity separation
  spec section 37 requires.
- Rationale and honest scope: no external cloud credential-issuance system exists in this
  environment, so these are self-issued, attempt-scoped short-lived credentials verified against
  this DB - not a claim of SSO/SPIFFE/Vault integration. Verifier-side presentation in a live
  campaign (real evaluator code calling the endpoint) remains an integration follow-up: the
  mechanism, delivery, and verification endpoint are implemented and the non-PG delivery proven;
  the DB-backed lifecycle is covered by `tests/test_attempt_credentials.py`.

**Codex follow-up review (2026-09-20), six findings closed at the time, four re-opened and
re-closed in a fifth review round** (same audit's hardening pass, prompt-15 continuation). (1) A
real credential-authorized capability was added - `GET /v1/attempts/{attempt_id}/candidate`
returns the persisted candidate only under a valid candidate/verifier credential for that attempt.
(2) All three child channels (engineering, BUILD, isolated VERIFY) now receive an explicit
allowlist-scrubbed environment (`_sanitized_child_env`), so worker secrets (`AIEB_DATABASE_URL`,
`*_TOKEN`, etc.) can never be inherited by candidate/evaluator code. (3) Issuance is lease-fenced:
migration `bc5e9d4b2107` adds `work_item_id`/`worker_id`/`lease_generation` to `attempt_credential`,
`issue_attempt_credential` refuses a stale worker (`LeaseFenceError`, row untouched), and
`revoke_attempt_credentials` runs in the reconciler's lease-expiry sweep inside the same locked
transaction - a crashed worker's tokens die at recovery, never lingering to their 1h TTL. (4) The
PG test seed no longer collides on unique identity constraints. (5) Invalid-token responses stop
leaking the roll's real expiry; malformed UUID paths are 404s. (6) Stale `IsolationPolicy`
docstring and trailing whitespace fixed. The suite now runs against the disposable
`aieb-test-postgres` container: `tests/test_attempt_credentials.py` 10/10,
`tests/test_attempt_lifecycle.py` 19/19 (incl. the environment-scrub regression). Single alembic
head `bc5e9d4b2107`, upgrade path green.

A FIFTH review round then found four of those closures incomplete and closed them: issuance was
not scoped to the credential's ATTEMPT nor to the work-item TYPE (cross-attempt minting and
role-blind minting refused - `LeaseFenceError`/`ValueError`, with `_WORK_ITEM_TYPE_ROLES`;
`engineering`->candidate, `verification`/`regrade`->verifier); a stale worker's delayed
`finally`-revoke is now FENCED by the issuing lease's stored `(work_item_id, worker_id,
lease_generation)` so it NO-OPS against a rotated token (`new_before_stale_revoke` /
`new_after_stale_revoke` controls green); the reconciler's sweep now covers CRASHED `regrade`
items too (its `reconcile_expired_leases` SELECT includes `"regrade"` and revokes both roles at
loop top); and verification now genuinely CONSUMES the capability - it issues the verifier
credential first and reads the persisted candidate through the credential-gated
`load_stored_candidate_authorized` (the same gate the endpoint enforces), revoking on every
infra-abort path after issuance. The endpoint is now verifier-ROLE-only (valid candidate-role
token -> 403) and returns the FULL `stored_candidate` payload. Also closed: the isolated VERIFY
spawn child imported the evaluator module at bootstrap, BEFORE the environment scrub -
`_run_verify_isolated` now passes the evaluator as (module, qualname) identity strings and the
entrypoint resolves the import only after scrubbing, and `_sanitized_child_env` strips
credentials embedded in allowlisted VALUES (URL userinfo, `?password=/token=/key=/secret=`).
Green at the fifth round: `tests/test_attempt_credentials.py` 14/14,
`tests/test_attempt_lifecycle.py` 21/21, `tests/test_worker_leasing.py` 38/38, the ENG-019 threat
model 39/39. Gap 3 remains in review pending the reviewer's negative-control run of these exact
controls; full detail in `sandbox-review.md` (fifth review round) and DECISIONS.md
ENG019-006/ENG020-007.

The reviewer's own negative run re-opened the import-time closure as a HIGH blocker: the HOSTED
WORKER's parent still imported the evaluator module (`runner_bridge.py` did
`importlib.import_module(evaluator_module)` to build the callable it passed into `run_verification`),
so evaluator top-level code ran against the worker's unsanitized env; the first regression only
imported its probe before planting secrets. Closed: evaluator identity (including qualname) now
lives IN `TASK_RUNTIMES` as a third tuple element and is threaded as plain `(module, qualname)`
strings from `runner_bridge` into the isolated spawn child; `runner_bridge` never imports the
module, `run_verification` accepts `evaluate_identity` directly (`TypeError` unless exactly one of
callable/identity), the isolated child is the FIRST process to import it, after the scrub. The
regressions now plant worker secrets BEFORE any probe import, exercise the real production paths
(identity strings; end-to-end leased engineering+verification via `execute_leased_work`), assert
the probe never enters the worker parent's `sys.modules`, and went RED (then reverted) against the
reintroduced parent import. Green after closure: worker leasing 39/39 (new
`test_hosted_verification_never_imports_the_evaluator_in_the_worker_parent`), lifecycle 21/21
(rewritten `test_import_time_env_leak_...` on the identity path), credentials 14/14, ENG-019
threat-model 39/39. Gap 3 still awaits the reviewer's re-run of the negative controls, including
the parent-import control.

### Migration rollback drill (spec section 43)
`scripts/migration_rollback_drill.py` - explicitly NOT limited to a schema round-trip on an
empty database. Two legs:
1. Upgrade to head, seed a REPRESENTATIVE dataset (task/entrant/campaign), downgrade one
   revision, upgrade back to head, verify the seeded rows survived intact.
2. Expand-phase COMPATIBILITY: checks out the repository's parent commit into a throwaway git
   worktree, imports ITS `aieb_api.models` module directly, and reads real rows from the
   CURRENT (post-migration) schema through the OLD ORM class definitions - proving old
   application code tolerates a freshly-migrated newer schema during a rolling deploy, not
   merely asserting the migration is additive.

### Backup/restore drill (spec section 40)
`scripts/backup_restore_drill.py` - a real `pg_dump`/`pg_restore` cycle between two separate
disposable databases, asserting the THREE specific behaviors spec section 40 names (not restore
duration, which is reported only as a disclosed local-proxy measurement):
1. An abandoned lease is still `leased` immediately after restore - not silently resumed.
2. Reconciliation run against the RESTORED database correctly recognizes and quarantines the
   orphaned lease (the same mechanism `tests/test_worker_leasing.py` already covers against a
   live connection, now proven to survive an actual restore).
3. A worker retrying with its stale pre-restore lease generation is fenced out after
   reconciliation and cannot commit results - the concrete form "revoke stale credentials"
   takes here. The Prompt-15 continuation pass ADDED the server-side credential store this
   report previously said none existed: `attempt_credential` rows (short expiry + explicit
   phase-end revocation) are what a phase's process actually presents for
   candidate/verifier identity - see "Scoped attempt credentials" above; the restore-drill's
   own pre-reconciliation fencing hole (a restored-backup DB that still shows a lease as live,
   letting a stale worker act before reconciliation refences it) is ledgered as the open gap 4
   from that same audit, not silently absorbed here.

### CI (spec section 42)
- `permissions: contents: read` added to all five workflows (the three pre-existing ones plus
  the two new ones below) - none previously declared least-privilege permissions.
- New `sandbox-integration.yml`: runs the ENG-019 sandbox/threat-model tests plus the cited
  ENG-003 SE-01 regression. Caches nothing at all, deliberately - the simplest way to satisfy
  "never cache hidden fixtures or candidate workspaces across runs" is to not cache anything a
  fixture or workspace could hide in.
- New `release-candidate.yml`: full uninterrupted backend discovery, OpenAPI/client staleness
  checks, the migration rollback drill, website build/test, and an SBOM generation step, on tag
  push or manual dispatch, plus a `capped-live-smoke` job gated behind a protected GitHub
  Environment requiring manual approval and carrying no secrets.
- New `dependency-review.yml` (separate from `release-candidate.yml` - see the review-round
  note above for why): runs on `pull_request` for dependency-file changes.

## Disclosed gaps in the CI work (not silently pinned)

- This repository's convention is to pin every GitHub Action by full commit SHA.
  `anchore/sbom-action` and `actions/dependency-review-action` are referenced by version tag
  in `release-candidate.yml`, with an explicit inline comment, because this offline environment
  cannot verify their exact current commit SHAs - a wrong guessed SHA would be worse (silent
  breakage or an unintended commit) than a disclosed gap. Pin both before this workflow ever
  runs with real credentials.
- `capped-live-smoke` is a structural placeholder only: it documents the required protected-
  environment gate and deliberately fails immediately with an explanatory warning, since no
  cloud provider or spend authorization exists to run a real live smoke test against (ADR-12).
- **Python lint/type-checking is not covered by any CI gate** (no ruff/mypy/pyright config
  exists anywhere in this repository). Not introduced this late, deliberately: `uv tool run
  ruff check .` was tried and found 400+ pre-existing findings across unrelated code; adding a
  new lint gate now would either fail immediately on that surface or scope-creep this phase's
  work into fixing many unrelated files. TypeScript IS type-checked (website build/type checks
  already run in `eng015-verification.yml`) - the gap is Python-only. See
  `release-candidate.yml`'s `full-gate-chain` job comment for the full disclosure.

## Verified results (actual, measured)

- Worker draining: `tests/test_worker_leasing.py::test_drain_signal_sets_the_stop_event` and
  `::test_drain_finishes_the_in_flight_item_and_never_claims_the_next_one` - **2/2 passed**. The
  second asserts lease integrity specifically: after drain, no work item is left `leased`, one
  is `done`, the second is still genuinely `ready` (never claimed), and a reconciler pass finds
  nothing to recover.
- Auto-pause: `::test_auto_pause_fires_after_three_consecutive_infrastructure_failures` (positive,
  using a real raising-verifier fixture matching the existing
  `test_verifier_outage_is_infrastructure_invalid_not_a_scored_fail` pattern),
  `::test_task_failures_alone_never_auto_pause_and_reset_the_counter` (negative, explicitly
  required),  `::test_an_infrastructure_streak_is_reset_by_one_non_infrastructure_outcome`, and
  `::test_run_worker_only_scores_auto_pause_on_verification_or_regrade_outcomes` (the
  work-type-gating regression) - **4/4 passed**.
- Kill switch: `::test_kill_switch_stops_all_new_dispatch_platform_wide` and (from the review
  round) `::test_kill_switch_tears_down_a_paused_campaign_too` - **2/2 passed**.
- Resume acknowledgement: `tests/test_api_service.py::
  test_resume_of_an_auto_paused_campaign_requires_explicit_acknowledgement` - **1/1 passed**
  (refusal without acknowledgement, then success with it, then confirms fields reset).
- Migration rollback drill, both legs, run after this work's own commit (`3b9debb`) provided a
  real commit boundary: **BOTH PASS**. Leg 1 (representative-dataset round-trip) against a
  dedicated disposable database, head revision `d3f6a8e21c94`. Leg 2 (parent-commit
  compatibility): the parent commit (`4a9c7c3`)'s `CampaignRow` ORM class, imported directly
  from a throwaway git worktree, read a real row through the post-migration schema without
  error - the expand-phase compatibility property, reproduced, not merely asserted.
- Backup/restore drill: **ALL THREE assertions PASS**, real `pg_dump`/`pg_restore` cycle between
  `aieb_restore_drill_source` and `aieb_restore_drill_restored`, restore completed in 0.95s
  (disclosed local-proxy measurement, not a production RPO/RTO figure - spec section 40's real
  targets, RPO <=15 minutes / RTO <=4 hours, require real staging/production infrastructure this
  environment does not have).
- Toolchain pinning across a simulated software upgrade:
  `tests/test_api_service.py::test_frozen_campaign_toolchain_stays_pinned_across_a_simulated_software_upgrade`
  - **1/1 passed**. Freezes a real campaign, registers a genuinely newer entrant revision under
  the same slug, and confirms the frozen campaign's manifest is completely unaffected, at both
  the database row and the API response.
- Scoped-credential env delivery and the DB-backed lifecycle: `tests/test_attempt_lifecycle.py`
  **21/21 passed** including `::test_engineering_extra_env_is_delivered_to_the_subprocess`
  (candidate token reaches the engineering subprocess environment),
  `::test_verification_attempt_vars_are_delivered_to_the_isolated_verify_subprocess` (verifier
  token reaches the spawn-based VERIFY subprocess and is echoed back by a module-level evaluator),
  and the fifth round's import-time env-leak control. `tests/test_attempt_credentials.py`
  **14/14 passed** against the `aieb-test-postgres` container (postgres:16, `localhost:5544`,
  `POSTGRES_PASSWORD=aieb_test_password`, `postgresql+psycopg`): lease-fenced issuance (stale
  takeover + expiry), revoked-by-reconciler crash sweep (engineering AND regrade), attempt/type-
  scoped issuance refusal, stale-`finally`-revoke no-op against a rotated token, full-payload
  verifier-only capability endpoint (cross-attempt 401, same-attempt candidate-role 403), role
  derivation, rotation, cross-attempt cross-role scoping, unknown-role rejection, and the
  token-authenticated verify endpoint via TestClient.
- Full backend regression after all of the above: see the commit's own verification note for
  the exact discovery-run count (this document is written before that final run completes, to
  keep the two artifacts in sync rather than back-filling a number after the fact).

## Remaining external acceptance (unchanged, disclosed)

- Real staging/production deployment, real deployed OIDC/JWKS, and a real live smoke test
  remain blocked on cloud authorization/budget - no infrastructure was provisioned or deployed
  by this work.
- Current-tree remote CI and independent review of this closure remain open, matching every
  other ticket's acceptance policy in this repository.
- Object storage/observability deployment (Prometheus, alerting) is documented in intent
  (spec section 39's targets) but not deployed - no monitoring infrastructure exists to deploy
  it to.
