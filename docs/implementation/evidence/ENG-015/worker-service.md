# ENG-015 evidence: PostgreSQL-leased execution/verification work

Date: 2026-09-14, revised 2026-09-16 (ENG015-007: engineering and verification
split into two independently leased phases). Local development/test evidence
only; no remote deployment occurred.

## What this ticket implements

- Atomic work acquisition (`aieb_api/worker/repository.py::claim_work_item`): a
  `SELECT ... FOR UPDATE SKIP LOCKED` CTE combined with an `UPDATE ... RETURNING`
  so two contending workers never claim the same ready `work_item`, without
  blocking on each other. `claim_work_item` claims across BOTH `engineering`
  and `verification` queues by default (a real worker pool services both
  phases, not a phase-dedicated one) - pass `work_type=` to isolate one queue.
- Generation/fencing: every state-changing call (`heartbeat`, `record_candidate`,
  `advance_to_verification`, `record_evaluation`, `finalize`) is a single
  atomic `UPDATE ... WHERE id=... AND worker_id=... AND generation=... AND
  state='leased'`, matching the pattern already proven for campaign
  draft/freeze in ENG-014. A worker whose lease has been reassigned
  (reconciled) cannot commit anything — its fenced check simply matches zero
  rows.
- Heartbeat/expiry: `work_item.lease_expiry` extended periodically by a
  background thread on its own DB session while the engineering or
  verification phase runs on the main thread outside any transaction.
- Two independently leased phases (ENG015-007): an `engineering` work item
  covers PROVISION→ENGINEER→STOP→COLLECT
  (`LocalAttemptRunner.run_engineering()`) and ends by persisting the
  collected candidate (`record_candidate`, artifact-first - before the work
  item is marked done) and enqueuing a brand new `verification` work item for
  the SAME attempt (`advance_to_verification`). A `verification` work item
  covers BUILD→VERIFY (`LocalAttemptRunner.run_verification()`), reconstructing
  the candidate from the artifact store plus the frozen source - never from
  the engineering phase's own `engineer/` workspace, which is already gone by
  then - possibly in a different process, possibly a different worker,
  possibly long after the engineering phase's own process exited. It persists
  the evaluation (`record_evaluation`, same artifact-first pattern) before its
  own `finalize` call.
- Reconciliation (`reconcile_expired_leases`): recovers an expired lease
  according to what that phase's own artifact-first evidence shows -
  `engineering` with a candidate already persisted advances straight to
  verification (never repeats engineering); `engineering` with no candidate is
  replaced with a brand-new attempt, up to the frozen campaign's
  `max_replacements`; `verification` with an evaluation already persisted
  finalizes from that evidence (no re-verification); `verification` with no
  evaluation is requeued as a fresh verification work item for the SAME
  attempt and candidate (a dead verifier never causes engineering to repeat),
  up to the same `max_replacements` cap.
- Cancellation (`cancel_campaign`, `is_campaign_cancelling`,
  `maybe_complete_cancellation`): stops new dispatch; a worker that claims
  engineering work for a cancelling campaign passes a set `cancel_event` into
  `LocalAttemptRunner.run_engineering()`, which polls that event during the
  engineering phase (a small, backward-compatible extension — see
  DECISIONS.md ENG015-002) instead of blocking on one uninterruptible
  `communicate(timeout=...)` call. Cancellation is checked only during
  engineering, not verification - a build+score pass is short and
  deterministic in this local-fixture scope, so there is no long-running
  verification step to interrupt yet.
- Orphan teardown (`reconciler._remove_orphan_allocations`): removes the
  writable `engineer`/`build` allocations a SIGKILLed worker (at either
  phase) never reached its own cleanup phase for; immutable `attempt.json`
  evidence is left untouched, mirroring `LocalAttemptRunner`'s own cleanup
  semantics exactly. Removing both unconditionally is safe even when only one
  allocation exists for a given phase.

## What this ticket reuses, not reimplements

Per the prompt's explicit instruction, this ticket adds no second execution or
scoring implementation. `aieb_api/worker/runner_bridge.py` is a thin bridge:
it resolves a leased work item to a task/evaluator, builds the same
`AttemptConfig` the local CLI builds (`aieb_cli.main._run`/`_editor`), and
calls `aieb_runner.lifecycle.LocalAttemptRunner.run_engineering()`/
`run_verification()` - the same pipeline logic as the CLI's `run()`, split
into two composable methods rather than duplicated (see DECISIONS.md
ENG015-007 and `packages/aieb-runner/src/aieb_runner/lifecycle.py`). A real
installed agent remains blocked on ENG-001's authorization gate; the
"engineering command" is the same deterministic candidate-variant editor the
CLI already uses for development verticals.

## What this ticket does not implement (tracked, not silently skipped)

- The public `POST /campaigns/{id}/start` endpoint (budget reservations, role
  checks): ENG-017. `repository.enqueue_frozen_campaign` is the internal
  trial/attempt/work_item expansion that endpoint will call; it is not itself
  wired to any HTTP route in this ticket.
- Exported OpenTelemetry spans/metrics: `worker/metrics.py` emits structured
  JSON log events with the IDs spec section 39 names and keeps in-process
  counters; wiring a real OTel exporter is hosted-observability infrastructure
  work, not this ticket's scope.
- Real object storage: candidate bytes still go through the existing local
  `FilesystemArtifactStore` (ENG-003), not S3. Object storage backend choice
  is unrelated to leasing/recovery, which is this ticket's job.

## Environment variables (no secrets)

| Variable | Purpose | Default |
| --- | --- | --- |
| `AIEB_DATABASE_URL` | Shared by `aieb-api`, `aieb-worker`, `aieb-reconciler` | none (fails closed) |
| `AIEB_WORKER_ID` | Identity used for fencing | `worker-<pid>-<random>` |
| `AIEB_WORKER_WORK_ROOT` | Local directory for engineering/build allocations and stored artifacts | `.aieb-worker-runs` |
| `AIEB_WORKER_POLL_SECONDS` | Idle poll interval when no work is ready | `1.0` |
| `AIEB_WORKER_LEASE_SECONDS` | Lease duration; heartbeat renews at 1/3 of this | `60` |
| `AIEB_WORKER_CANDIDATE_VARIANT` | Which fixture variant the deterministic editor applies (`reference`/`alternative`/`baseline`) | `reference` |
| `AIEB_RECONCILER_POLL_SECONDS` | How often the reconciler sweeps expired leases | `10` |

## Local multi-worker launch commands

```powershell
docker run -d --name aieb-test-postgres -e POSTGRES_PASSWORD=aieb_test_password -e POSTGRES_DB=aieb_test -p 5544:5432 postgres:16
$env:AIEB_DATABASE_URL = "postgresql+psycopg://postgres:aieb_test_password@localhost:5544/aieb_test"
cd services/api; ..\..\.venv\Scripts\python.exe -m alembic upgrade head; cd ..\..

# Enqueue a frozen campaign's trials (until POST /campaigns/{id}/start exists in ENG-017):
.\.venv\Scripts\python.exe -c "from aieb_api import db; from aieb_api.worker import repository; db.configure(); import sys; repository.enqueue_frozen_campaign(db.session_factory()(), sys.argv[1])" <campaign-id>

# Start several workers against the same database - each claims a different
# ready work item; contention is real, not simulated:
$env:AIEB_WORKER_ID = "worker-1"; Start-Process .\.venv\Scripts\aieb-worker.exe
$env:AIEB_WORKER_ID = "worker-2"; Start-Process .\.venv\Scripts\aieb-worker.exe
$env:AIEB_WORKER_ID = "worker-3"; Start-Process .\.venv\Scripts\aieb-worker.exe

# Start the reconciler (recovers expired leases, tears down orphan allocations):
.\.venv\Scripts\aieb-reconciler.exe
```

## Test evidence (real PostgreSQL, real subprocess kills)

`tests/test_worker_leasing.py` (14 tests) runs against the same disposable
Postgres container as ENG-014's tests and covers every controlled-failure
scenario the prompt names, not just the happy path - including the five
ENG015-007 names explicitly for the leasing split:

| Scenario | Test |
| --- | --- |
| Two workers contending for work | `test_two_workers_never_claim_the_same_item` |
| Death before launch | `test_death_before_launch_is_replaced_not_double_counted` |
| Death during engineering | `test_death_during_engineering_real_subprocess_kill_is_replaced_and_orphans_removed` (a real OS process, killed) |
| Engineering death after candidate persistence | `test_engineering_death_after_candidate_persistence_advances_to_verification` |
| Verification death after evaluation recorded | `test_verification_death_after_evaluation_recorded_is_resumed_not_requeued` |
| Verifier death with no evaluation (retry without re-engineering) | `test_verifier_death_with_no_evaluation_retries_verification_without_re_engineering` |
| Stale verifier returning after lease reassignment | `test_stale_verifier_cannot_record_or_finalize_after_lease_reassignment` |
| Lease expiry, old (engineering) worker returns | `test_stale_worker_finalize_after_lease_reassignment_is_rejected` |
| Verifier outage (trusted scorer crash) | `test_verifier_outage_is_infrastructure_invalid_not_a_scored_fail` |
| Duplicate verification completion | `test_duplicate_finalize_call_is_a_no_op_not_a_double_score` |
| Cancellation and orphan teardown | `test_cancelled_campaign_attempt_is_not_engineered_and_cleans_up`, `test_cancellation_arriving_mid_run_interrupts_the_attempt` |
| Fencing under real concurrency | `test_concurrent_reconciler_cannot_race_a_record_candidate_still_in_flight`, `test_reconciler_never_orphans_a_lease_a_live_worker_just_re_extended` |

`tests/test_attempt_lifecycle.py::test_engineering_and_verification_can_run_as_two_independent_calls`
proves the split's core guarantee directly at the `LocalAttemptRunner` level:
construct a fresh `AttemptOutcome` carrying only the persisted candidate
reference (as a different worker/process would after loading it from the
database) and call `run_verification()` on it independently of the
`run_engineering()` call that produced it, confirming it produces the same
verdict as the composed `run()`.

The "death during engineering" test spawns a real Python subprocess that
claims a work item and starts a deliberately slow (20-second) engineering
script, kills the process mid-sleep with `Process.kill()`, then verifies: the
work item is still `leased` (the killed process never finalized), no
`CandidateRow` was ever recorded (nothing was collected yet), the orphaned
`engineer/` allocation the killed process's own cleanup never removed is
found and removed by `teardown_orphan_allocations`, and reconciliation
correctly replaces the attempt rather than resuming it.

`tests/test_attempt_lifecycle.py::test_cancel_event_stops_engineering_before_deadline_with_no_verdict`
covers the `LocalAttemptRunner` cancellation extension directly.

## Post-review fixes (2026-09-14)

An independent review found two genuine TOCTOU races, both fixed and covered
by tests that call the real functions under genuine concurrent execution:

- **`record_outcome`'s fencing check was a plain `SELECT`**, taking no row
  lock under READ COMMITTED. A concurrent reconciler sweep could expire the
  same lease and create a replacement attempt between that check and
  `record_outcome`'s own commit, letting an already-abandoned worker's
  results land anyway. Fixed by making the fencing check a real
  `UPDATE ... WHERE ... RETURNING` (which also extends the lease), so it
  takes the same row lock the reconciler's `SELECT ... FOR UPDATE SKIP
  LOCKED` contends for. `test_concurrent_reconciler_cannot_race_a_record_outcome_still_in_flight`
  calls the real `record_outcome`, pausing it mid-transaction (via a
  `before_commit` session event) while a genuine concurrent reconciler sweep
  runs in a second thread — verified to fail against the pre-fix code before
  being kept as a permanent regression test.
- **Orphan-file cleanup came from a separate, disconnected, unlocked read**
  (`teardown_orphans`), not the reconciler's own locked decision. A live
  worker whose heartbeat was merely delayed could have its files deleted even
  though its heartbeat succeeds moments later. Fixed by having
  `reconcile_expired_leases` return `orphaned_attempt_ids` — exactly the
  attempts it just committed as replaced, from inside the same locked pass —
  and having the reconciler delete files only for those IDs.
  `test_reconciler_never_orphans_a_lease_a_live_worker_just_re_extended`
  confirms a lease a real heartbeat call just extended is never touched.

See DECISIONS.md ENG015-006.

## Handoff

Worker leasing, fencing, heartbeat, reconciliation, cancellation, and now
(ENG015-007) two independently leased phases are implemented and tested
against a real PostgreSQL instance. Wiring `enqueue_frozen_campaign` to a
real `POST /campaigns/{id}/start` endpoint with budget reservations, and
exporting real OTel metrics remain ENG-017/ENG-016 dependencies. Local CLI
functionality is unaffected; `aieb_runner.lifecycle.LocalAttemptRunner`
gained one backward-compatible optional parameter (`cancel_event`, ENG015-002)
and, for ENG015-007, two new public methods (`run_engineering`,
`run_verification`) - `run()` itself is now a thin wrapper composing them, so
existing callers (the local CLI) see no behavior change.
