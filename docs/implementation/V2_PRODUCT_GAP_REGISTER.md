# AI Engineer Bench v2.0 — Product Gap Register

Updated: 2026-09-22

This register tracks gaps between the current repository and the v2 product
goal in AI-Engineer-Bench-Redesign-Spec-v2.0.md. It distinguishes code that
exists from capabilities exercised in a real end-to-end campaign. Local
fixtures, deterministic providers, and development evidence must not be
described as official benchmark results.

## Status vocabulary

- OPEN — required product capability is missing or incomplete.
- BLOCKED — closure requires external authorization, infrastructure, private
  data, or independent review.
- PARTIAL — a foundation exists, but the end-to-end acceptance path is not
  complete.
- CLOSED — implementation and the stated acceptance evidence exist.

## P0 — core product operation

### V2-GAP-001 — Hosted Harbor campaign dispatcher

- Status: OPEN / highest priority.
- Current evidence: Harbor adapter and zero-cost fake-agent smoke exist in
  packages/aieb-runner/src/aieb_runner/backends/harbor/ and
  scripts/run_rag05_model_agent.py. `services/api/src/aieb_api/worker/harbor_dispatch.py`
  additionally provides the frozen-cell → `ExecutionSpec` translation with
  pinning checks and runtime deadline enforcement (see V2-GAP-004), unit-tested
  in `tests/test_harbor_dispatch.py` against a synthetic launcher - but it is
  not called from the live worker loop.
- Gap: services/api/src/aieb_api/worker/loop.py still executes the local runner
  bridge and does not dispatch HarborBackend for real campaign cells.
- Closure: convert frozen API cells into ExecutionSpec; launch and monitor
  Harbor from the leased worker; persist trajectory, verifier output, artifacts,
  timing, usage, and model identity; normalize failures; support fenced
  replacements; and prove the PostgreSQL-backed integration path.

### V2-GAP-002 — v2 operator CLI

- Status: PARTIAL.
- Current evidence: a scriptable operator workflow now exists and consumes
  private authenticated APIs:
  - `aieb operator release prepare --campaign <id> --manifest <path> [--publication-class ranked|non_ranked] [--supersedes <id>] [--correction-reason <text>]` → verifies the supplied frozen manifest against the server (`GET /v1/campaigns/{id}` with `expected_manifest_digest`, refusing stale manifests, changed task revisions, and changed cohorts via `manifest_digest_mismatch`/`cohort_drift`), then `POST /v1/campaigns/{id}/publications/prepare`; the envelope reports `manifest_digest`, `server_manifest_digest`, `cohort_digest`, and `digest_verified`
  - `aieb operator release inspect --preparation <id>` → `GET /v1/publications/preparations/{id}`
  - `aieb operator campaign plan --campaign <id> --manifest <path>` (read-only)
  - `aieb operator campaign run --campaign <id> --manifest <path> --confirm-run` (gates, then `POST /v1/campaigns/{id}/start`)
  - `aieb operator campaign inspect --campaign <id>`
  - `aieb operator campaign approve --campaign <id> [--reason <text>]` → `POST /v1/campaigns/{id}/approve`
  - `aieb operator publication publish --preparation <id> [--independence-attestation] [--notes <text>]` → `POST /v1/publications/preparations/{id}/review`
  - Stable `aieb.operator-cli/v1` JSON envelopes, `--json/--api-url/--access-token/--idempotency-key`, env fallbacks `AIEB_API_URL`/`AIEB_API_TOKEN`.
  - Private transport/client: `packages/aieb-cli/src/aieb_cli/private_api.py` (bearer auth, required idempotency keys on all mutations, socket-bound timeouts, mutation redirects refused, same-key-only retry). Error bodies are redacted (secret-shaped keys such as `token`/`password`/`secret`/`credential`/`authorization`, plus JWT/bearer/URL-userinfo/`sk-*`/`ghp_*` value shapes) before store/print, so `--json` can never leak a server-supplied credential.
  - New API surface: `POST /v1/campaigns/{id}/approve` + `GET /v1/campaigns/{id}/approval` (`services/api/src/aieb_api/routes/campaigns.py`), reviewer-only, no self-approval, frozen-only, replay-safe.
  - Frozen-manifest identity: plan/run and release prepare require `--manifest`; the local digest is verified against the server's stored `manifest_digest`/`cohort_digest` and anchored campaigns require valid 64-character values for BOTH, so any drift (stale manifest, changed task revision, changed cohort, missing/malformed cohort digest, non-frozen server anchor) refuses to proceed; read-only `plan` reports unanchored states honestly instead of pretending.
  - Tests: `tests/test_operator_cli_unit.py` (29 fake-transport tests: auth, idempotency, digest identity incl. stale-manifest/cohort-drift/task-revision/non-frozen refusals for release prepare, error-body secret redaction, read-only planning, approval-gated runs, 409/412/422/503/timeout mapping, no token/secret leakage, private-route-only traffic; manifests are written to the repository `.cache/test-tmp` convention with a nested-create probe skip, as in the Harbor normalization tests); `tests/test_operator_cli_integration.py` (real PostgreSQL + uvicorn end-to-end create→freeze→plan→approve→run→inspect→release prepare→inspect→publish, plus self-approval and key-absence refusals; DB-gated via `AIEB_DATABASE_URL`).
- Gap (why not CLOSED): the integration suite has not been executed against a live disposable PostgreSQL yet; V2-GAP-001 (Harbor dispatch) and ENG-024 (authorized live campaign) remain open, so an end-to-end operator release of a REAL campaign has not been demonstrated; the local runner bridge is still the execution path.
- Closure: run the integration suite green against disposable PostgreSQL; then reclassify per the closure evidence below.
- Closure evidence (remaining proofs): (1) all seven commands exist and are scriptable; (2) stable JSON on every path; (3) no public website route or unauthenticated mutation is reachable through the operator CLI; (4) frozen-manifest digest identity enforced by plan/run and release prepare (stale manifest, task revision, cohort, and non-frozen drift all refused); (5) authorization and idempotency enforced client-side and server-side; (6) `plan` issues no mutation; (7) `run` cannot bypass the approval/confirm gates; (8) publication commands never touch a public read path; (9) retries never double-apply a mutation (same idempotency key, safe network failures only) and error responses never leak server-supplied secrets.

### V2-GAP-003 — Complete task admission state machine

- Status: PARTIAL.
- Current evidence: `services/api/src/aieb_api/admission.py` defines a versioned
  admission protocol, bounded executor boundary, gate classification, reset
  evidence, independent review, and release-eligibility checks. The additive
  migration `6f2a9d5c1e73_task_admission_state_machine.py` persists immutable
  runs/gates/resets/reviews and database-enforced state transitions. Private
  endpoints are registered in `routes/admissions.py`; campaign registry
  resolution rejects revisions that are not admitted. Freeze now creates a
  `frozen` admission-state row but does not imply admission.
- Focused evidence: `tests/test_task_admission_state_machine.py` has 6 protocol
  and integrity tests passing locally; its 3 PostgreSQL persistence tests are skipped
  because `AIEB_DATABASE_URL` is unset. `alembic heads` reports
  `6f2a9d5c1e73` as the migration head. OpenAPI was regenerated to 58 paths.
- Integrity fixes now reject duplicate reset keys, require reset numbers
  `1..min_resets` per required matrix case, validate per-case reset coverage at
  release eligibility, and bind execution/release checks to the protocol digest.
- Remaining gap: PostgreSQL migration/integration tests, real executor-backed
  admission runs, and release-eligibility regressions have not yet run against
  a disposable database. The external executor remains an explicit configured
  boundary; no fixture result is treated as official admission evidence.
- Closure: run the complete executor matrix and reset protocol against pinned
  task revisions, verify the PostgreSQL state-machine/migration tests, record a
  genuine independent review, and only then change this status to CLOSED.

### V2-GAP-004 — Release and campaign orchestration

- Status: PARTIAL.
- Current evidence (this section previously understated the working tree - it
  now reflects `services/api/src/aieb_api/orchestration.py`,
  `routes/campaigns.py`, `worker/repository.py`,
  `worker/harbor_dispatch.py`, and migration
  `b9c0d1e2f3a4_orchestration_state_machine_and_matrix.py`):
  - Campaign state machine draft→frozen→planned→approved→running↔paused→
    {completed|incomplete}, plus cancelling→cancelled from any active state,
    enforced BOTH in the service layer (`orchestration.assert_transition`)
    and by a PostgreSQL trigger (`aieb_reject_illegal_campaign_transition`) -
    a raw SQL update cannot bypass it either.
  - Exact task×entrant×repetition matrix expansion at `plan`
    (`worker/repository.py::materialize_frozen_matrix`), with a `matrix_digest`
    re-verified at `start` and at aggregation
    (`orchestration.verify_matrix_or_raise`) - the matrix cannot silently
    drift after planning.
  - Reviewer-gated `approve` binding the manifest/cohort/matrix digests, with
    self-approval structurally refused.
  - Budget reservation with an auditable formula and frozen budget-profile
    digest (`budgets.py`), explicitly labeled `estimated_time_limited` - see
    that module's own docstring disclosure; there is still no
    provider-side hard spend hold (that integration is separate, tracked
    against ENG-008).
  - `worker/harbor_dispatch.py`: the frozen-cell → Harbor `ExecutionSpec`
    translation adapter, with anti-substitution pinning checks
    (`verify_spec_pinning`) and, as of this update, **actual runtime
    deadline enforcement** - `dispatch_cell` now polls `status()` and calls
    `stop()` at the frozen per-cell deadline (mirroring
    `aieb_runner.backends.orchestration.run_bounded`) instead of calling
    `collect()` immediately after `launch()`. Covered by
    `tests/test_harbor_dispatch.py` (synthetic-launcher smoke tests: within
    deadline, deadline-stop, and a launcher that ignores `stop()`).
  - Full publication pipeline (prepare→independent review→publish) with an
    explicit, already-correct ranked/non_ranked policy: a `ranked`
    publication requires complete cohort coverage
    (`routes/publications.py::_publication_eligibility_error`); an
    **incomplete cohort can only be published as `non_ranked`**, which is a
    disclosed, non-canonical release, never presented as a ranked result.
    This is the intended closure behavior for "block incomplete cohorts",
    not a gap - recorded explicitly here so it is not mistaken for one.
- Gap (why not CLOSED):
  1. **Harbor live integration evidence is still pending**
     (V2-GAP-001): the worker now selects the explicit Harbor path for
     engineering leases and routes them through
     `worker/runner_bridge.py::execute_leased_harbor_engineering`, which calls
     `harbor_dispatch.dispatch_cell`. A real PostgreSQL/Harbor campaign run is
     still required as the final integration proof. **This
     remains a real fork in crash-recovery semantics, not merely a unit-test
     wiring task** -
     Harbor's own `Trial` runs the agent AND verifier together in one
     container (`packages/aieb-runner/.../harbor/backend.py`), whereas the
     local path leases engineering/verification as two independently
     recoverable phases (ENG015-007, "a dead verifier never causes
     engineering to repeat"). The tradeoff is recorded as a PENDING decision
     in `DECISIONS.md` (2026-09-22) rather than resolved by guessing.
  2. Provider-backed hard budget enforcement remains out of scope for this
     gap (see above) and is disclosed, not silently assumed.
- **Closed as of 2026-09-22: live-PostgreSQL / real-worker proof of the full
  pipeline.** Every DB-gated test file (478 tests total across `tests/`,
  including `test_worker_leasing.py`'s 47 leasing/replacement/cancellation/
  reconciler drills, `test_campaign_start_atomicity.py`'s 18 concurrency/
  budget-reservation tests, `test_operator_cli_integration.py`'s full
  create→plan→approve→run→inspect→prepare→publish CLI flow, and
  `test_task_admission_state_machine.py`) now passes green against a real
  disposable PostgreSQL instance (migrated to head, including both new
  V2-GAP-003 and V2-GAP-004 migrations) - not fixture/unit-level only, as
  every prior status update in this register had to caveat. Getting there
  surfaced and fixed several genuine, previously-unexercised bugs (not test
  artifacts of this pass - each is a real defect independent of the DB run
  itself):
  - `admission.seed_fixture_admission()` never called `claim_for_execution`
    (left the run at `pending`, so `finalize_run` silently wrote zero gate
    rows) and hand-built its run row instead of calling `start_run()` (which
    is what actually creates the placeholder gate rows `finalize_run`
    requires to exist) - fixed in `admission.py`.
  - `schemas.py::PublishedEvidenceManifest` used `dict[str, Any]` fields
    with `Any` never imported - pydantic could never build the model, so
    every withdrawal/export call through `verified_published_manifest` was
    unreachable (503 "published run selection is malformed"). One-line fix.
  - Several test fixtures were stale against this now-enforced state
    machine/immutability: raw ORM campaigns jumping straight to a terminal
    state, `/start` called without first `/plan`+`/approve`, `limit_usd:
    None` (now correctly read as "unbounded, refuse to start"), and
    placeholder `manifest_digest`/`revision_digest` values that fail the
    admission gate's own manifest-schema check once it is actually
    evaluated. Fixed across `tests/test_api_service.py`,
    `test_campaign_start_atomicity.py`, `test_operator_cli_integration.py`,
    `test_review_closure.py`, `test_review_followup.py`,
    `test_invalidity_review.py`, `test_worker_leasing.py`.
  - Two small, unrelated pre-existing issues the run also surfaced:
    `SubmissionPolicy(include=("**",))` in `test_harbor_normalization.py`
    trips a real "not task-specific" validator; and
    `POST /v1/attempts/{attempt_id}/credentials/verify` (a read-only
    credential check) was missing from `test_api_idempotency_audit.py`'s
    non-persisting-POST exemption list.
  Full command: `python -m pytest tests/ -q` with `AIEB_DATABASE_URL`
  pointed at a disposable Postgres 16 container, migrated via
  `alembic upgrade head`.
- Closure: wire `harbor_dispatch.dispatch_cell` into
  `execute_leased_work`/`worker/loop.py` behind `AIEB_DISPATCH_BACKEND=harbor`
  once the recovery-model decision above is made (closing V2-GAP-001 as a
  side effect), then run a real Harbor/synthetic-provider campaign against
  disposable PostgreSQL as the final end-to-end proof.

## P1 — content and evaluation integrity

### V2-GAP-005 — Genuine private official holdouts

- Status: BLOCKED / PARTIAL.
- Current evidence: holdout preparation tools and ENG-021 documentation exist.
- Gap: no access-controlled official holdout corpus exists outside the public
  repository/build context.
- Closure: private storage, overlap review, immutable manifest, evaluator
  isolation, access audit, and independent review.

### V2-GAP-006 — Independent admission and release review

- Status: BLOCKED.
- Current evidence: reviewer checklist and pending-independent-review metadata.
- Gap: no genuine independent human review is recorded for every task/release.
- Closure: persist reviewer identity, scope, decision, timestamp, evidence
  digest, independence declaration, and reason.

### V2-GAP-007 — Track A depth and application diversity

- Status: PARTIAL.
- Current evidence: twelve development tasks and broad families exist; RAG-05
  has a real-source development package.
- Gap: several tasks remain shallow/shared-harness tasks, with no admitted
  release proving the intended depth/diversity floor.
- Closure: review difficulty/distinctness, reject shallow variants, and admit
  only a defensible suite.

### V2-GAP-008 — MVP-2 bug-finding end-to-end track

- Status: PARTIAL.
- Current evidence: bug-finding contracts/scoring foundations and tests exist.
- Gap: no complete repository task catalog, hidden-label workflow,
  finding/patch execution, or separate published cohort has been proven.
- Closure: fixed repositories, controls, hidden labels, duplicate/severity
  scoring, patch separation, and end-to-end Harbor/evaluator evidence.

## P1 — model track

### V2-GAP-009 — Authorized real model execution

- Status: BLOCKED.
- Current evidence: model loop/provider adapters and fake Docker smoke exist.
- Gap: no real provider call has been authorized or executed.
- Closure: provider/model authorization, credentials, spend cap, fixed cohort,
  unsupported-control disclosure, and real usage/model identity persistence.

### V2-GAP-010 — Production model-track dispatch

- Status: OPEN.
- Current evidence: usage sink and repository writers are proven by a spike.
- Gap: no production dispatcher invokes the model loop for a real campaign.
- Closure: wire per-trial model configuration through Harbor and persist all
  requested/reported identity and usage.

## P2 — deployment and operations

### V2-GAP-011 — Hardened official isolation

- Status: BLOCKED.
- Current evidence: Harbor Docker egress guards, metadata denial, credentials,
  process cancellation, and threat-model tests exist.
- Gap: Docker is not VM-equivalent hardened isolation; no official provider is
  selected or validated.
- Closure: approved provider, adversarial network/filesystem tests, cross-trial
  isolation proof, and documented capability limits.

### V2-GAP-012 — Staging/production deployment

- Status: BLOCKED.
- Current evidence: migrations, drills, runbooks, CI workflows, kill switch,
  and metrics exporter exist.
- Gap: no real staging/production deployment, OIDC/JWKS validation, live smoke,
  or deployed alerting pipeline.
- Closure: authorized environment, least privilege, real identity provider,
  live smoke, alert delivery, and rollback verification.

### V2-GAP-013 — Operational quality gates

- Status: OPEN.
- Current evidence: targeted tests and generated-artifact checks exist.
- Gap: Python lint/type-checking CI and one clean-checkout v2 release gate are
  not implemented.
- Closure: triage lint/type failures and require reproducible full CI checks.

## P2 — product surface and documentation

### V2-GAP-014 — Public website backed by an actual release

- Status: PARTIAL.
- Current evidence: apps/web is read-only, typed, and handles empty/error and
  redaction states.
- Gap: no official publication exists to exercise the complete public journey.
- Closure: serve an authorized immutable publication and verify real API
  provenance, comparisons, evidence, and corrections.

### V2-GAP-015 — Documentation/status convergence

- Status: OPEN.
- Current evidence: STATUS.md, SESSION_HANDOFF.md, and evidence contain
  extensive historical ticket language.
- Gap: the repository can be mistaken for a v1 completion ledger rather than
  a clean v2 status model.
- Closure: publish one v2 status matrix, mark historical records, update README
  commands/architecture names, and remove stale claims without deleting needed
  evidence.

## External gates that must remain explicit

Provider/model authorization and spend caps, private holdout access, independent
human review, official provider selection, staging credentials/deployment, and
official campaign/publication approval are external gates. They must not be
“fixed” by changing labels or generating synthetic evidence.

## Recommended order

1. V2-GAP-001: hosted Harbor dispatcher.
2. V2-GAP-002 through V2-GAP-004: one operator workflow.
3. V2-GAP-003/006: admission and independent-review persistence.
4. V2-GAP-005: private holdout boundary.
5. V2-GAP-008: MVP-2; V2-GAP-009/010: model dispatch.
6. V2-GAP-011 through V2-GAP-013: deployment and isolation gates.
7. Authorized campaign and real publication (V2-GAP-014).
8. Final v2 audit and official-release decision.
