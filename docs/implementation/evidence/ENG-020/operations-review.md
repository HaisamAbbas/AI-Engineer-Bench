# ENG-020 — Staging/production CI/CD, backups, and runbooks

Date: 2026-09-18. No real cloud budget/authorization exists in this environment (same
constraint ENG-001/ENG-019 disclose). This closes what is genuinely implementable and testable
without provisioning paid infrastructure or deploying anything public, and explicitly names
what stays blocked.

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
   takes here, since no separate server-side credential store exists to revoke.

### CI (spec section 42)
- `permissions: contents: read` added to all five workflows (the three pre-existing ones plus
  the two new ones below) - none previously declared least-privilege permissions.
- New `sandbox-integration.yml`: runs the ENG-019 sandbox/threat-model tests plus the cited
  ENG-003 SE-01 regression. Caches nothing at all, deliberately - the simplest way to satisfy
  "never cache hidden fixtures or candidate workspaces across runs" is to not cache anything a
  fixture or workspace could hide in.
- New `release-candidate.yml`: full uninterrupted backend discovery, OpenAPI/client staleness
  checks, the migration rollback drill, website build/test, and an SBOM generation step, on tag
  push or manual dispatch. Includes a `dependency-review` job (PR-triggered) and a
  `capped-live-smoke` job gated behind a protected GitHub Environment requiring manual
  approval and carrying no secrets.

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
- Kill switch: `::test_kill_switch_stops_all_new_dispatch_platform_wide` - **1/1 passed**.
- Resume acknowledgement: `tests/test_api_service.py::
  test_resume_of_an_auto_paused_campaign_requires_explicit_acknowledgement` - **1/1 passed**
  (refusal without acknowledgement, then success with it, then confirms fields reset).
- Migration rollback drill leg 1 (representative-dataset round-trip): **PASS**, run against a
  dedicated disposable database (`aieb_rollback_drill`), head revision `d3f6a8e21c94`.
  Leg 2 (parent-commit compatibility) requires a real git commit boundary and could not be
  exercised until this work itself is committed - see the follow-up note below.
- Backup/restore drill: **ALL THREE assertions PASS**, real `pg_dump`/`pg_restore` cycle between
  `aieb_restore_drill_source` and `aieb_restore_drill_restored`, restore completed in 0.95s
  (disclosed local-proxy measurement, not a production RPO/RTO figure - spec section 40's real
  targets, RPO <=15 minutes / RTO <=4 hours, require real staging/production infrastructure this
  environment does not have).
- Full backend regression after all of the above: see the commit's own verification note for
  the exact discovery-run count (this document is written before that final run completes, to
  keep the two artifacts in sync rather than back-filling a number after the fact).

## Remaining external acceptance (unchanged, disclosed)

- Real staging/production deployment, real deployed OIDC/JWKS, and a real live smoke test
  remain blocked on cloud authorization/budget - no infrastructure was provisioned or deployed
  by this work.
- Migration rollback drill leg 2 (the parent-commit compatibility check) must be re-run once
  this work is committed, against the real commit boundary it needs.
- Current-tree remote CI and independent review of this closure remain open, matching every
  other ticket's acceptance policy in this repository.
- Object storage/observability deployment (Prometheus, alerting) is documented in intent
  (spec section 39's targets) but not deployed - no monitoring infrastructure exists to deploy
  it to.
