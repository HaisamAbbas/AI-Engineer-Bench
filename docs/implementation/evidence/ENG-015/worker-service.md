# ENG-015 evidence: PostgreSQL-leased execution/verification work

Date: 2026-09-14, revised 2026-09-16 (ENG015-007: engineering and verification
split into two independently leased phases; ENG015-008: a further review of
that split found four real gaps - verification cancellation, stored-candidate
digest verification, legacy-row handling, and idempotent artifact-first
writes - all fixed, plus one topology claim narrowed; ENG015-009: a fourth
review found ENG015-008's own fixes for findings #1/#2/#3/#5 were each only
partial, plus one new gap (#4) - all fixed for real this time: cancellation
now genuinely interrupts a running BUILD/VERIFY via a background thread, not
merely checked before/after; idempotent retries now return the AUTHORITATIVE
persisted evaluation/candidate rather than trusting the caller's own retry
payload, and a genuine content conflict now raises rather than silently
succeeding; storage/reference corruption during BUILD is now classified
infrastructure_invalid, not a candidate contract violation; a malformed
nested stored-candidate field now raises the typed error via a Pydantic
envelope, not a bare AttributeError; and shared storage is now a real
PostgreSQL-backed `PostgresArtifactStore`, the hosted worker's actual
default, not merely a documented local-filesystem limitation.
ENG015-010 (fifth review): the same review's four findings, all fixed.
(1) Cancellation could still not TERMINATE a running verification - the
phase thread was abandoned on cancel and `run_verification`'s finalize deleted
the build directory under it. Verification now runs evaluators under a
cooperative cancellation contract: every evaluator receives a stop Event,
an evaluator honouring it is joined cleanly (never classified a scorer
error via the typed `CancelledError`), and only an evaluator that ignores
the stop event past a bounded grace (EVALUATOR_CANCEL_GRACE_SECONDS) is
abandoned as a daemon thread - with the build allocation deliberately LEFT
IN PLACE for host-side reconciliation instead of deleted under the live
thread. Legacy single-argument evaluators are still supported via a
signature-dispatch helper. (2) The PostgreSQL blob backend violated ADR-08
and had no size bound, retention class, or orphan expiry - the superseding
ADR-11 (recorded in DECISIONS.md, since the frozen spec files are
hash-pinned) documents the deviation explicitly, and the schema enforces the
submission policy's 52 MiB cap at both the store and the database
(ck_worker_artifact_blob_max_bytes), splits staging from committed evidence
(retention_class/staged_until), ties every reference to its candidate
(candidate_id FK), and the reconciler purges unreferenced staging blobs
after 24 hours while never deleting evidence or referenced blobs (spec
sections 37/38). (3) A divergent evaluation retry (a second "fail" for an
identity recorded as "pass") was silently treated as an idempotent replay -
it now raises EvaluationConflictError (mirroring record_candidate's
CandidateConflictError), lands in a durable audit_event row, and the worker
finalizes infrastructure_invalid while the first persisted evaluation stays
authoritative. (4) The stored-candidate envelope now validates STRICTLY
(extra=forbid, strict=True, digest/visibility/path/length constraints, path
uniqueness), with exactly one documented lenient field (the UUID id the
writer serializes as a JSON string).
ENG015-011 (a sixth review, reproducing failures directly rather than only
reading code): ENG015-010's own fixes for findings #1/#2 had NOT actually
closed what they claimed to, plus four more real defects, all fixed. (1) The
cancellation race persisted: an evaluator that IGNORED the cooperative stop
event but happened to finish naturally DURING the grace window still came
back `completed=True` with nothing recording that cancellation had fired -
reproduced directly with a 1.5s-sleeping fixture against the 5s default
grace - so its late, unrequested result was silently scored. `PhaseRun`
gained a `cancelled` flag set the INSTANT cancellation is observed,
independent of `completed`, checked first and unconditionally by every
caller. (2) Containment was still theoretical: a Python thread can never be
preempted, so "abandon" was the only option, not the spec's actual
emergency-cancellation requirement to kill active runs. VERIFY's evaluator
now runs in an OWNED, forcibly-killable `multiprocessing` (spawn)
subprocess (`LocalAttemptRunner._run_verify_isolated`) - `terminate()`,
escalating to `kill()`, then joined, so the process is confirmed dead
before the call returns; a related BUILD-phase teardown race (an abandoned
thread's target directory not yet created at the moment `build.exists()`
was checked, then created after cleanup already reported clean) was fixed
by deciding preservation on `phase_abandoned` alone, never also on
`build.exists()`. (3) Migration `e20d5d09b489` - already committed in a
prior commit - had been rewritten in place to add columns/constraints; any
database that had already applied it would never receive them, while the
ORM would expect them regardless, and a fresh base-to-head test cannot
detect this. Restored to its original committed content; a new migration
(`f2b6c9a417de`) adds `retention_class`/`staged_until`/`candidate_id` and
their constraints/indexes via `ALTER TABLE`. (4) Orphaned staging artifacts
never actually expired: the purge treated ANY reference (even an unclaimed
one) as protection, but `collect_candidate` always creates a blob AND a
reference together, so a real orphan always has exactly such a reference
and was never purged in practice - the existing test had synthesized an
orphan as a referenceless blob, which doesn't reproduce the real path.
Since `attach_candidate_references` always flips a blob to `'evidence'` the
moment ANY reference on it is claimed, a still-`'staging'` blob's
references are, by construction, all unclaimed - the purge now deletes them
before deleting their blob; the reconciler's engineering-crash recovery path
(advance straight to verification when a candidate was already persisted)
also never called `attach_candidate_references` at all - fixed by calling
it there too. (5) The database size CHECK only compared the caller-supplied
`byte_length` against the cap, never the real `octet_length(data)` -
reproduced directly with a mismatched INSERT; fixed with
`octet_length(data) = byte_length AND octet_length(data) <= MAX_WORKER_ARTIFACT_BYTES`.
(6) Reference visibility used an unanchored regex (`re.match` semantics
accepted `"public-evil"`) - fixed with `Literal["public", "restricted"]`.
Local development/test evidence only; no remote deployment occurred.

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
  either an `engineering` or a `verification` work item for a cancelling
  campaign passes a set `cancel_event` into `LocalAttemptRunner.run_engineering()`
  or `run_verification()` respectively. `run_verification()` runs BOTH BUILD
  and VERIFY on a background thread, polled against `cancel_event`
  (`LocalAttemptRunner._run_cancelable`, ENG015-009) - cancellation
  interrupts a phase that is actually running, not merely checked
  before/after it (ENG015-008's own fix only checked the boundary between
  phases, which a third review correctly identified as still letting a
  cancellation arriving strictly DURING BUILD or VERIFY complete and persist
  a score).

  ENG015-010 (fifth review finding #1) fixed the two ways that mechanism
  still fell short of spec section 16's "emergency cancel kills active
  runs": (a) the abandoned daemon thread kept EXECUTING - running candidate
  subprocesses, consuming resources, making external calls - even though
  its result was discarded; and (b) `run_verification`'s finalize could
  `shutil.rmtree` the build allocation while that thread was still reading
  it. Verification evaluators now run under a cooperative cancellation
  contract: `_run_cancelable` passes each phase worker a stop Event, an
  evaluator that honours it returns promptly as the typed
  `aieb_runner.lifecycle.CancelledError` (a BaseException, so a broad
  `except Exception` can never misreport cancellation as a scorer error)
  and its thread is JOINED cleanly; only an evaluator that ignores the stop
  event past `EVALUATOR_CANCEL_GRACE_SECONDS` is abandoned as a daemon
  thread - and `run_verification` then deliberately leaves the build
  allocation in place for host-side reconciliation instead of deleting it
  under the live thread (`cleanup_clean=False` records the leftover; the
  abandoned thread's eventual result is still discarded and never scored).
  Legacy single-argument evaluators keep working through a
  signature-dispatch helper (`_invoke_evaluator`). Engineering cancellation
  was already hard (the runner owns and kills its subprocess tree via
  `_stop_tree`); hard termination of a misbehaving verification evaluator
  is documented as requiring a caller-owned subprocess, since Python
  threads cannot be preempted. `test_cooperative_cancellation_during_verify_joins_the_evaluator`
  and `test_uncooperative_evaluator_is_abandoned_without_teardown_race`
  prove both paths deterministically (the evaluator itself triggers the
  cancel, so it lands strictly inside VERIFY).
- Stored-candidate integrity (ENG015-008/009, review findings #2/#3/#4):
  before a verification phase builds or scores a candidate reconstructed
  from persisted JSON, it recomputes that candidate's manifest digest and
  tree hash and compares them against `CandidateRow`'s own authoritative
  `manifest_digest`/`tree_digest` columns - a mismatch (corruption, a
  hand-edit, a bug elsewhere) routes to `infrastructure_invalid` rather than
  evaluating under a falsified identity. That check covers the MANIFEST but
  not `file_references`' own metadata (reference id, blob digest/length,
  access scope) - a third review correctly noted corrupting those still
  passed both checks and reconstruction's resulting `ArtifactError` was
  misclassified as a candidate `CONTRACT_VIOLATION`. Fixed at the
  classification, not by duplicating validation: `run_verification()`'s
  BUILD phase now treats ANY `ArtifactError` as `infrastructure_invalid`
  (attribution `HOST_FAILURE`) - by BUILD, the candidate's actual submission
  content was already accepted as contract-compliant during COLLECT, so an
  error reconstructing it now is always a storage/reference integrity
  failure, never the candidate's own fault; `reconstruct_candidate()`
  itself already re-verifies every file's actual bytes against the
  manifest's own per-file digest (which the outer manifest-digest check
  protects), so this closes the gap for reference id/blob/scope corruption
  too, not just the manifest. A missing or malformed `stored_candidate`
  (e.g. a legacy row's `{}` server_default, OR a malformed nested field like
  `"id": []`) is now validated as a whole through a typed Pydantic envelope
  (`_StoredCandidateEnvelope`) rather than manual dict indexing plus
  `uuid.UUID(...)` - any structural or type mismatch anywhere in the payload
  raises one well-defined `StoredCandidateUnavailableError`, never a bare
  `KeyError`/`AttributeError` escaping to crash the worker process (a third
  review reproduced exactly the `AttributeError` case directly).
- Idempotent, conflict-aware artifact-first writes (ENG015-008/009, review
  finding #2): `record_candidate`/`record_evaluation` catch the
  `IntegrityError` their own unique constraints (`uq_candidate_attempt_tree`,
  `uq_evaluation_plan_digest`) raise on a retried call whose prior commit
  actually succeeded but whose acknowledgement was lost. ENG015-008's first
  fix stopped there - returning success without comparing the retry's
  payload against what was actually persisted, so a retry that computed a
  genuinely DIFFERENT verdict (or candidate content) under the identical
  identity could still get silently accepted and even finalize from its own,
  wrong, in-memory result instead of the real persisted one. Fixed
  properly: `record_evaluation` now returns a `RecordedEvaluation` -
  ALWAYS the authoritative persisted verdict/result, whether newly written
  or already there - and `runner_bridge.py` finalizes from THAT, never from
  its own local `outcome.verdict`; `record_candidate` compares the full
  persisted payload (manifest_digest, validation_status, stored_candidate)
  against the retry's own, and raises `CandidateConflictError` on any real
  mismatch instead of silently returning a different row's id as if it were
  the one just recorded.
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
- ~~Real distributed object storage~~ - RESOLVED (ENG015-009). ENG015-008
  narrowed this to "shared/network filesystem across every worker host,"
  which a third review correctly rejected as still unimplemented by
  default - documenting a requirement is not meeting it. The hosted worker
  now stores candidate bytes in PostgreSQL itself
  (`aieb_api/worker/artifact_store.py::PostgresArtifactStore`, backed by
  the new `worker_artifact_blob`/`worker_artifact_reference` tables,
  migration `e20d5d09b489`) instead of `FilesystemArtifactStore` - the same
  `AIEB_DATABASE_URL` every worker already needs to lease work at all, not
  a second infrastructure dependency (no S3/object-store client, no
  operator-provisioned network mount). `execute_leased_engineering`/
  `execute_leased_verification` construct this store directly; the local
  CLI is unaffected (no database at all) and still uses
  `FilesystemArtifactStore` exclusively.

  Storing candidate bytes in PostgreSQL is an EXPLICIT, documented deviation
  from ADR-08 ("PostgreSQL for hosted metadata, object storage for
  artifacts"): the superseding decision, recorded as "ADR-11" in
  `docs/implementation/DECISIONS.md` (the frozen spec files are hash-pinned
  by `scripts/dev.py`, so the supersession lives in the decisions ledger,
  not in an edited architecture doc), supersedes ADR-08 for
  candidate-staging artifacts until ENG-019 delivers
  the S3-compatible end state. The guardrails the frozen specs attach to
  artifact storage are enforced (ENG015-010, fifth review finding #2):
  the submission policy's own 52 MiB cap is checked at the store boundary
  AND by the `ck_worker_artifact_blob_max_bytes` database CHECK;
  `retention_class` distinguishes staging from committed evidence;
  `staged_until` stamps the spec section 37 24-hour expiry on staging
  blobs; `worker_artifact_reference.candidate_id` ties every reference to
  the candidate that created it (claimed by
  `repository.attach_candidate_references` inside the same fenced lease
  that records the candidate, promoting its blobs to non-expiring
  'evidence'); and the reconciler runs
  `repository.purge_expired_worker_artifacts`, which deletes ONLY
  unreferenced, expired staging blobs - committed evidence and blobs with
  remaining live references are never removed (spec sections 37/38).
  `test_worker_artifact_size_cap_is_enforced_at_store_and_database` and
  `test_staging_blobs_expire_after_24h_but_committed_evidence_never_purged`
  prove both behaviors against real Postgres.
  `test_verification_recovers_the_candidate_with_no_shared_filesystem_at_all`
  proves this directly: engineering and verification run against
  COMPLETELY SEPARATE, never-shared local directories (simulating two
  hosts with no filesystem in common whatsoever), and verification still
  recovers and correctly scores the candidate from Postgres alone.
  `test_engineering_and_verification_can_run_as_two_independent_calls`
  (in `test_attempt_lifecycle.py`, testing `aieb_runner.lifecycle` directly
  rather than the hosted worker) still uses `FilesystemArtifactStore` with
  a second, independently-constructed instance pointed at the same
  directory - that is a deliberate, narrower test of the generic
  `LocalAttemptRunner`/`ArtifactStore` abstraction itself (which the local
  CLI also relies on), not a claim about the hosted worker's own storage
  choice.

## Environment variables (no secrets)

| Variable | Purpose | Default |
| --- | --- | --- |
| `AIEB_DATABASE_URL` | Shared by `aieb-api`, `aieb-worker`, `aieb-reconciler` | none (fails closed) |
| `AIEB_WORKER_ID` | Identity used for fencing | `worker-<pid>-<random>` |
| `AIEB_WORKER_WORK_ROOT` | Local scratch directory for engineering/build process allocations only (`runs/`, `engineer/`, `build/`) - candidate BYTES themselves live in PostgreSQL (`PostgresArtifactStore`, ENG015-009), not here, so this directory need not be shared across workers | `.aieb-worker-runs` |
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

`tests/test_worker_leasing.py` (25 tests) runs against the same disposable
Postgres container as ENG-014's tests and covers every controlled-failure
scenario the prompt names, not just the happy path - including the five
ENG015-007 names explicitly for the leasing split, five from the ENG015-008
review (verification cancellation, digest mismatch, legacy/malformed
stored_candidate, and duplicate candidate/evaluation recording), and two more
from the ENG015-009 review (a cross-host topology proof with no shared
filesystem at all, and cancellation genuinely interrupting a running VERIFY
via the background poll thread rather than an already-set event) plus fixes
for the two record_candidate/record_evaluation conflict scenarios and the
reference-corruption/malformed-nested-field cases below:

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
| Verification claimed after cancellation (ENG015-008 #1) | `test_verification_cancellation_after_handoff_is_not_scored` |
| Stored candidate diverged from its own recorded digests (ENG015-008 #3) | `test_stored_candidate_digest_mismatch_is_infrastructure_invalid_not_evaluated` |
| Legacy/malformed `stored_candidate` row (ENG015-008 #4) | `test_legacy_empty_stored_candidate_is_infrastructure_invalid_not_a_crash` |
| Duplicate candidate/evaluation recording under an ambiguous commit (ENG015-008 #5) | `test_duplicate_record_candidate_call_returns_the_same_row_not_an_integrity_error`, `test_duplicate_record_evaluation_call_is_treated_as_already_recorded` |
| Cancellation genuinely interrupts a RUNNING VERIFY, not just an already-set event (ENG015-009 #1) | `test_verification_cancellation_arriving_mid_verify_interrupts_the_attempt` |
| A conflicting evaluation retry never overrides the first persisted verdict (ENG015-009 #2) | `test_record_evaluation_returns_the_first_persisted_verdict_not_a_conflicting_retry` |
| A conflicting candidate retry raises rather than silently succeeding (ENG015-009 #2) | `test_record_candidate_raises_on_a_genuine_content_conflict` |
| Corrupted stored reference (not the manifest) is infrastructure_invalid, not a candidate fault (ENG015-009 #3) | `test_corrupted_reference_id_is_infrastructure_invalid_not_a_candidate_contract_violation` |
| Malformed nested reference field never raises a bare AttributeError (ENG015-009 #4) | `test_malformed_nested_reference_is_infrastructure_invalid_not_an_attribute_error` |
| Verification recovers a candidate with NO shared filesystem at all (ENG015-009 #5) | `test_verification_recovers_the_candidate_with_no_shared_filesystem_at_all` |

`tests/test_attempt_lifecycle.py::test_engineering_and_verification_can_run_as_two_independent_calls`
proves the split's core guarantee directly at the `LocalAttemptRunner` level:
it round-trips the collected candidate through the real
`_serialize_stored_candidate`/`_deserialize_stored_candidate` JSON (exactly
what `runner_bridge.py` persists to and reads back from PostgreSQL), builds a
fresh `AttemptOutcome` from the deserialized result, and calls
`run_verification()` through a SECOND, independently-constructed
`FilesystemArtifactStore`/`LocalAttemptRunner` pointed at the same root
directory - not the original in-process objects `run_engineering()` used
(ENG015-008: the original version of this test reused the same store/runner
object, which only proved the split works within one process, not the actual
cross-process/shared-storage claim). It confirms this produces the same
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

Worker leasing, fencing, heartbeat, reconciliation, cancellation that
genuinely TERMINATES a running VERIFY (an owned, forcibly-killable
subprocess, not merely an abandoned thread whose result is discarded -
ENG015-011), stored-candidate integrity checking (manifest AND reference
metadata, via correct failure classification rather than duplicated
validation), conflict-aware idempotent artifact-first writes (a genuine
retry conflict is now surfaced, never silently accepted), a real
PostgreSQL-backed shared artifact store with real size/retention/expiry
enforcement at the database level (`PostgresArtifactStore` - the hosted
worker's actual default, not a documented local-filesystem limitation),
orphaned staging artifacts that actually expire (references included, not
just blobs that happen to have none), and two independently leased phases
(ENG015-007, hardened by ENG015-008, ENG015-009, ENG015-010, and ENG015-011)
are implemented and tested against a real PostgreSQL instance. Wiring
`enqueue_frozen_campaign` to a real `POST /campaigns/{id}/start` endpoint
with budget reservations, and exporting real OTel metrics remain
ENG-017/ENG-016 dependencies. Local CLI functionality is unaffected: it has
no database at all and continues to use `FilesystemArtifactStore`
exclusively; `aieb_runner.lifecycle.LocalAttemptRunner` gained one
backward-compatible optional parameter (`cancel_event`, ENG015-002/
ENG015-008) on both `run_engineering()` and `run_verification()`, a private
`_run_cancelable()` helper (ENG015-009, hardened by ENG015-011's `cancelled`
flag) BUILD uses internally to poll that event against a background thread,
a `_run_verify_isolated()` helper (ENG015-011) VERIFY uses instead to run
the evaluator in an owned subprocess it can forcibly terminate, and, for
ENG015-007, two public methods (`run_engineering`, `run_verification`) -
`run()` itself is a thin wrapper composing them, so existing callers (the
local CLI) see no behavior change beyond genuine cancellation now working
correctly. Full regression: 106 tests across `test_api_service.py` (49),
`test_worker_leasing.py` (29), `test_attempt_lifecycle.py` (12), and
`test_analysis.py` (16) pass against real PostgreSQL; `test_ext_tool_admission.py`
(which also runs real attempts through `LocalAttemptRunner`) passes
unchanged.
