# Reconciliation runbook

Scope: local/hosted PostgreSQL-leased execution workers (ENG-015). Matches
the runbook format in spec section 48 (incident, first action, closure).

## Normal operation

`aieb-reconciler` runs continuously (default: every 10 seconds,
`AIEB_RECONCILER_POLL_SECONDS`), independent of any worker process. On each
sweep it:

1. Finds `work_item` rows of type `engineering` still `state='leased'` whose
   `lease_expiry` has passed.
2. For each, checks whether a `candidate` row already exists for the
   attempt (durable evidence the worker got as far as collecting a
   candidate before dying):
   - **Yes** → completes finalization from that evidence (using the
     recorded `evaluation`, if any). No re-execution, no duplicate scoring.
   - **No** → marks the old attempt/work_item terminal
     (`infrastructure_invalid`) and creates a replacement attempt/work_item,
     up to the frozen campaign's `max_replacements`. Beyond that limit, the
     trial is left unresolved (no further replacement is created) rather
     than retried forever.
3. Separately removes local `engineer`/`build` allocations for those same
   expired attempts — the writable directories a SIGKILLed worker's own
   cleanup phase never reached. Immutable `attempt.json` evidence is left in
   place.

Nothing here requires killing or restarting worker processes; a reconciler
sweep and a live worker holding a *valid* lease never interfere with each
other (the fenced `UPDATE` a worker later attempts against an already-failed
work item simply matches zero rows).

## Incident: worker process disappeared (crash, OOM-kill, host loss)

| Step | Action |
| --- | --- |
| Detect | A `work_item` remains `leased` past its `lease_expiry`; the next reconciler sweep (within `AIEB_RECONCILER_POLL_SECONDS`) picks it up automatically. No manual detection step is required in normal operation. |
| First action | None required — do not manually edit `work_item` rows. If the reconciler itself appears to be down, restart it; it is stateless and idempotent (a sweep run twice on the same expired lease is a no-op the second time, since the first sweep already transitioned the row out of `leased`). |
| Verify | Query `select id, state, terminal_status from attempt where trial_id = :trial` to confirm the old attempt is `terminal` and, if replaced, a new attempt/work_item exists with `number = old.number + 1`. |
| Closure | If `exhausted` was reported for the trial (replacement limit reached), the trial is unresolved for this campaign — this is expected, not a bug; ENG-011's analysis package already treats a missing planned trial as "no canonical complete rank," so no further action is needed to keep results honest. |

## Incident: reconciler falsely treats a live, slow worker as dead

| Step | Action |
| --- | --- |
| First action | Confirm the worker process is actually still running and its heartbeat thread hasn't silently died (check the worker's own `worker.claimed`/heartbeat-adjacent log lines). If the heartbeat thread died but the engineering subprocess is still running, the lease will correctly expire and be reconciled — this is not a false positive, it is the fencing design working as intended: a worker that cannot prove it is alive must not be trusted to finalize. |
| Recovery | If leases are expiring too aggressively for genuinely long engineering deadlines, raise `AIEB_WORKER_LEASE_SECONDS` (and therefore the heartbeat interval, which is derived as `lease_seconds / 3`) rather than disabling reconciliation. |

## Incident: reconciler itself crashes or falls behind

| Step | Action |
| --- | --- |
| First action | Restart it. It holds no in-memory state between sweeps; every sweep re-derives what needs recovering entirely from `work_item.lease_expiry` and the presence/absence of `candidate` rows. |
| Recovery | If a backlog of expired leases has built up, the next sweep processes all of them in one pass (bounded by the `SELECT ... FOR UPDATE SKIP LOCKED` on that query, which lets a second reconciler instance safely run concurrently without double-processing the same row). |

## Orphan local disk allocations

`teardown_orphan_allocations` only removes `<work_root>/<attempt_id>/runs/attempt-*/{engineer,build}`
for attempts whose lease is expired and still `leased` in the database — it
never deletes anything for an attempt already `done`/`failed` (those were
already cleaned up by the worker's own normal cleanup phase, or are
retained as `failed` evidence with nothing left to remove). This makes it
safe to run repeatedly and safe to run before, after, or interleaved with
`reconcile_expired_leases` in any order.

## What this runbook does not cover

- Official VM-level isolation, cross-trial network denial, and cloud-metadata
  access controls remain ENG-019's threat-model work; this runbook is a local
  development-adapter recovery procedure, not a claim of official sandbox
  isolation (matching ENG-006/007's existing documented limitation).
- Budget/reservation reconciliation after a crash is ENG-008's role-separated
  accounting concern, tracked separately from work-item leasing.
