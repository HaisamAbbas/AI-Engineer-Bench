# ENG-020 — Operational runbooks (spec section 48)

Each entry ties the spec's named incident to real, existing mechanisms in this codebase - not
aspirational steps. None of these has been exercised against real production traffic (none
exists); each cites the test that exercises the underlying mechanism.

## Provider outage

1. Activate the kill switch to stop new dispatch platform-wide while investigating:
   `repository.activate_kill_switch(session, activated_by_user_id=<admin>, reason="provider outage")`.
   This also requests bounded teardown of active work via the existing cancellation machinery
   (`repository.cancel_campaign` on every non-terminal campaign).
   Tested: `tests/test_worker_leasing.py::test_kill_switch_stops_all_new_dispatch_platform_wide`.
2. Review `WorkerHeartbeatLossExceedsTarget`/`SetupFailureRateElevated` alerts
   (`deploy/alerts/prometheus-rules.yml`) to confirm scope.
3. Once the provider is confirmed healthy (or replaced under the frozen protocol rule - never
   silently substituted mid-campaign), deactivate: `repository.deactivate_kill_switch(session)`.
   This does NOT resume any campaign the kill switch drove to `cancelling`/`cancelled`, and does
   NOT clear any campaign's own `auto_paused` flag - both are separate operator decisions.

## Spend exceeds reservation

1. The kill switch is the immediate stop (see above) if spend is actively runaway.
2. For a single campaign whose infrastructure keeps failing (a common precursor to runaway
   spend from repeated retries), auto-pause already stops it automatically after
   `AUTO_PAUSE_INFRASTRUCTURE_FAILURE_THRESHOLD` (3) consecutive verification/regrade
   infrastructure failures - `services/api/src/aieb_api/worker/repository.py::
   record_infrastructure_outcome`. Tested:
   `tests/test_worker_leasing.py::test_auto_pause_fires_after_three_consecutive_infrastructure_failures`.
3. Reconcile actual spend against `budgets.reservation_summary()` before resuming anything.
4. Resuming an auto-paused campaign requires explicit operator acknowledgement:
   `POST /v1/campaigns/{id}/resume {"acknowledge_auto_pause": true}` - fix the underlying cause
   first; a plain resume is refused with 403. Tested:
   `tests/test_api_service.py::test_resume_of_an_auto_paused_campaign_requires_explicit_acknowledgement`.

## Worker disappears

1. `reconcile_expired_leases`/`reconcile_once` (`services/api/src/aieb_api/worker/reconciler.py`)
   already runs on a poll loop and fences a dead worker's lease via its generation number - a
   returning stale worker cannot commit results (`repository.finalize`'s fenced UPDATE). Tested
   extensively in `tests/test_worker_leasing.py` (orphan-teardown section) and, specifically
   through a real backup/restore cycle, in `scripts/backup_restore_drill.py`.
2. Orphaned local allocations (engineer/build directories a killed worker's own cleanup never
   reached) are torn down by the same reconciliation pass (`orphaned_attempt_ids`).
3. Classify the affected attempt's invalidity honestly (`infrastructure_invalid`) rather than
   scoring a candidate that never ran under real conditions.

## Database restored from backup

A `pg_restore` resurrects every pre-restore lease and credential exactly as it was - a lease
whose `lease_expiry` is still in the future looks live, and a pre-restore worker with the right
generation can heartbeat, finalize, and issue/use credentials before the reconciler's next poll
(and a stale worker heartbeating it would keep that poll from ever fencing it). The system fence
epoch closes this: every lease/credential is stamped with the epoch under which it was claimed/
issued, every fenced operation requires that stamp to equal the CURRENT epoch, and the reconciler
sweeps stale-epoch leases on its next poll even while they are still renewable.

1. **Establish the barrier - HALF the barn door before the fence moves.** Set the kill switch
   (`repository.activate_kill_switch(session, activated_by_user_id=<admin>, reason="restore drill")`)
   so NO new dispatch is possible, and STOP the reconciler loop AND every worker process (or
   network-isolate workers at the load balancer) so no already-running worker holds an open
   transaction across the advance. The epoch advance itself is atomic even against a worker that
   slips through (deciding-review finding 1: fenced operations share the fence row FOR SHARE for
   their whole transaction, and the advance's exclusive lock waits) - the barrier is the
   defense-in-depth that makes an operator deliberately aware of every actor, not the only line.
2. Restore the database, verify it (migration head, a migration rollback drill), then pre-flight:
   `python scripts/fence_advance.py --check` - read-only, prints the fence epoch and confirms the
   kill-switch barrier is in place.
3. **Advance the fence** with the DEDICATED, executable, operator-DB-role-authenticated command:
   `python scripts/fence_advance.py --reason "restore drill 2026-09-20" [--by-user <operator-uid>]`.
   It REFUSES to advance while the kill switch is inactive, requires the audit `--reason`, and
   re-checks the barrier inside the transaction. After it prints `fence epoch advanced: N -> N+1`,
   every pre-restore lease and credential is fenced at first touch - no reconciliation required.
4. Let the reconciler's next poll recover the stale-epoch orphans (replacing/requeuing per each
   phase's artifact-first evidence) and revokes their credentials inside the same pass. Health-check
   the queue before opening traffic.
5. Resume: clear the kill switch (`repository.deactivate_kill_switch(session)`) and restart the
   reconciler/workers only after dispatch reaches a verified steady state. Before/while resuming,
   run `scripts/backup_restore_drill.py` - a real `pg_dump`/`pg_restore` cycle proving the whole
   sequence: the in-window lease survives restore as `leased`, the fence advance fences the
   pre-restore worker at FIRST touch (heartbeat/issue/finalize refused BEFORE reconciliation), the
   pre-restore TOKEN is dead and its STATUS agrees (finding 2), the reconciler quarantines the
   stale-epoch lease, and a fresh worker claims and heartbeats under the new epoch.

## Scorer defect

1. Block publication of any snapshot pinning an evaluation the defective scorer produced -
   `routes/publications.py`'s eligibility checks already require re-validating each pinned
   evaluation against `build_evidence_manifest` before a publication can be prepared.
2. Fix or version the evaluator; a regrade re-executes the corrected, trusted scoring bundle
   over the RETAINED candidate bytes without re-engineering (ENG-018,
   `regrading.py::enqueue_regrade`).
3. Either regrade to a superseding publication (`supersedes_publication_id`) or withdraw the
   affected publication (`POST /v1/publications/{id}/withdraw`) - both already implemented and
   tested (`tests/test_review_closure.py`, `tests/test_review_followup.py`).

## Hidden fixture exposed

1. Quarantine: revoke access to the exposed fixture path immediately (outside this codebase -
   a registry/secrets-manager action, not an application code path).
2. Rotate the held-out fixture/evaluator to a new version; `aieb task validate` already rejects
   a task whose `evaluator_digest` no longer matches its recomputed content
   (`tests/test_accounting_and_cli.py::test_stale_content_digest_fails_validation`), so a
   rotated fixture is detected structurally, not just by policy.
3. Record the correction via the existing `correction_run`/`review` machinery (ENG-018) - the
   exposure and its remediation become part of the campaign's disclosed provenance, never
   silently erased.

## Incorrect public score

1. Freeze the affected snapshot: publications are already immutable once published (migration
   `c9a1e7d4b260`'s trigger freezes identity/campaign/class/timestamp/terminal status while
   still allowing `status`/`reason` transitions).
2. Publish a superseding snapshot with a corrected evaluation selection and a required
   `correction_reason` (distinct from a withdrawal reason - both are separately recorded and
   surfaced in the corrections UI, per `docs/implementation/evidence/ENG-018/review-closure.md`
   item 6).
3. The prior publication's `status` moves to `superseded`, never deleted - readers can always
   see what changed and why.
