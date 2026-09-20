# ENG-020 — Exact staging validation steps

Prompt 15 requires these specifically because cloud is unavailable here: this is the ordered
procedure an operator executes against a REAL staging environment on the day cloud
authorization exists, converting "blocked" into "ready to run." Nothing on this page has been
run against real infrastructure - `deployment-topology.md` covers the descriptive
components/topology/DR targets; this page is the deliverable that makes those actionable.

Every command below already exists and is exercised locally in this repository (cited, not
invented for this page); only the target (a real staging host/database instead of a local
disposable one) is new.

## 0. Preconditions

- [ ] Staging PostgreSQL instance provisioned, `AIEB_DATABASE_URL` for it held as a deployment
      secret (never in a PR-triggered CI run - see `deployment-topology.md`'s environments
      section).
- [ ] `AIEB_OIDC_ISSUER`/`AIEB_OIDC_JWKS_URL`/`AIEB_OIDC_AUDIENCE` point at a real staging IdP
      (not the `AIEB_ENV=test` fixture provider, which refuses to construct outside
      `AIEB_ENV=test` by design).
- [ ] `AIEB_PUBLICATION_SIGNING_KEY` is a real, durable Ed25519 key for staging (not the
      ephemeral per-process key the app falls back to when unset).
- [ ] Images built from `deploy/docker/Dockerfile.api` and `Dockerfile.worker`, tagged by an
      immutable digest (git SHA or content digest, never `latest` - spec section 45).
- [ ] The kill switch is confirmed OFF (`repository.is_kill_switch_active` returns `False`)
      before any of the following steps - a stale-active kill switch would silently block
      every dispatch check below and could be mistaken for a deploy failure.

## 1. Deploy

1. Push the built API/worker images to the staging registry.
2. Roll out the API deployment first (stateless, no schema dependency until step 2 runs).
3. Do NOT yet roll out workers - they must not start claiming work against a not-yet-migrated
   schema.

## 2. Migrate

1. Run `alembic upgrade head` against the staging database (exactly
   `uv run --directory services/api alembic upgrade head`, the same command
   `eng015-verification.yml` and `release-candidate.yml` already run against their disposable
   databases).
2. Confirm the reported head revision matches what CI verified
   (`scripts/migration_rollback_drill.py`'s leg 1 already proved this exact migration chain
   round-trips against a representative dataset; leg 2 already proved the immediately prior
   application version tolerates this schema - this step is that same chain, now against the
   real staging database instead of a disposable one).
3. Roll out worker deployments now that the schema is current.

## 3. Verify (control-plane health, no work dispatched yet)

1. `GET /openapi.json` on the staging API responds 200 (confirms the process is actually
   serving, matches the readiness probe pattern `eng015-verification.yml` already uses
   locally).
2. `repository.is_kill_switch_active(session)` against the staging database returns `False`
   (confirms migration didn't leave a stale kill-switch row from a prior drill or incident -
   see Precondition 0).
3. Confirm worker processes are running and have logged `worker.start` (see
   `services/api/src/aieb_api/worker/metrics.py::log_event`) - not yet claiming real work,
   just confirming the drain-capable process (`install_drain_handlers`) is alive.
4. Confirm least-privilege database roles: the worker's `AIEB_DATABASE_URL` role cannot
   `DROP`/`ALTER`/`TRUNCATE` any table (`deployment-topology.md`'s "Least-privilege
   credentials" section names exactly which tables it needs and which it must never touch) -
   attempt a `DROP TABLE` as that role and confirm it is rejected.

## 4. Smoke (capped, real work, real spend bound)

1. Freeze a single-trial, single-repetition campaign against a real (non-fixture) but
   deliberately cheap entrant/task pair, with an explicit, small `per_role_budget_usd` cap.
2. Start it; confirm the budget reservation is created
   (`budgets.reservation_summary()`) and its estimated amount matches
   `reserved_amount_from_resolved()`'s formula.
3. Confirm the trial reaches a terminal state (`completed` or `incomplete`) within a bounded
   wait, and that its reservation is marked `consumed`, not left `active`.
4. This is exactly the "capped smoke" spec section 42's release-candidate gate names -
   `release-candidate.yml`'s `capped-live-smoke` job is the CI-side placeholder for this exact
   step; this manual procedure is what fills it in once real spend/provider authorization
   exists.
5. Confirm the campaign's publication path is reachable (prepare -> review -> publish a
   non-ranked, clearly-labeled staging snapshot) and that its evidence export contains no
   private fields (the same assertions `tests/test_review_closure.py`/
   `test_review_followup.py` already make locally).

## 5. Rollback (only if step 3 or 4 fails)

1. Point traffic back at the previous compatible API image (spec section 45: "Immutable image
   digests permit rollback").
2. Do NOT downgrade the database unless the new migration is confirmed incompatible with the
   previous application version - `scripts/migration_rollback_drill.py`'s leg 2 exists
   specifically to have already answered this before deploy, so a rollback under this
   procedure should almost always be an image-only rollback, not a schema downgrade.
3. If a schema downgrade IS required: `alembic downgrade -1` (or to the specific prior
   revision), then confirm with `alembic heads` that the previous application version's own
   migration head is now current.
4. Never cancel a scored run merely to hide a bad outcome (spec section 45) - a rollback
   addresses the deployment, not the campaign's recorded evidence.
5. If the rollback was a DATABASE restore, advance the system fence epoch with the operator
   command BEFORE resuming any dispatch: set the kill switch (barrier), then
   `python scripts/fence_advance.py --check` (pre-flight) and
   `python scripts/fence_advance.py --reason "post-rollback fence advance"` - see the
   "Database restored from backup" runbook. Then run `scripts/backup_restore_drill.py`'s FIVE
   assertions (restored lease still `leased`, pre-restore worker fenced at FIRST touch with its
   credential dead and status agreeing, reconciliation quarantines the stale-epoch orphan, stale
   generation finalize refused, fresh claim works) against the ROLLED-BACK state before resuming
   normal dispatch, exactly as they were proven against a restored database in this repository's
   own drill.

## 6. Post-incident (if steps 1-5 were triggered by a real incident, not a routine deploy)

Follow the matching runbook in `runbooks.md` (Provider outage / Spend exceeds reservation /
Worker disappears / Scorer defect / Hidden fixture exposed / Incorrect public score).
