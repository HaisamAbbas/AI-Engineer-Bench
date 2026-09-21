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
  scripts/run_rag05_model_agent.py.
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
- Current evidence: services/api/src/aieb_api/routes/authoring.py supports
  draft creation, source metadata, updates, and freezing.
- Gap: freezing is not full admission. Clean checkout, control matrix, reset,
  leakage, and independent-review gates are not a complete persisted state
  machine.
- Closure: persist immutable admission runs/digests, require all controls and
  resets, require independent reviewers, and make only admitted revisions
  release-eligible.

### V2-GAP-004 — Release and campaign orchestration

- Status: PARTIAL.
- Current evidence: campaign/publication persistence, analysis, and integrity
  checks exist.
- Gap: no single path runs from admitted catalog to frozen release, Harbor
  matrix, campaign approval, and publication.
- Closure: freeze versions/protocol/resources, expand exact cells, enforce
  deadlines/budgets, retain invalid/replacement attempts, block incomplete
  cohorts, and produce immutable manifests.

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
