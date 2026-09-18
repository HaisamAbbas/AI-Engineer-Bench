# ENG-018 — Prompt 14 staging implementation

Date: 2026-09-17. Branch: `prompt-14-eng017-eng018`.

Final status (2026-09-18): COMPLETE after independent technical acceptance.
See `review-closure.md`. Historical `IN_PROGRESS` and open-list statements below
are retained as dated execution history, not the current ticket status.

**Latest working-tree review:** [Nine-point closure and real-browser acceptance](review-closure.md)
records the subsequent fixes, 110-test backend batch, 88-test frontend suite,
and authenticated browser/API flow. Open-item lists below describe their dated
historical checkpoints; see that supplement for current scope and limitations.

## Implemented

- Prepare derives the snapshot and selection from the same authoritative aggregation.
- Review rejects approval by the campaign creator or preparer for both disclosed
  review kinds. Approval signs and inserts a publication atomically.
- Withdrawal retains snapshots; public export reuses whitelist-redacted evidence.
- Regrade executes the installed trusted evaluator over retained candidate bytes,
  creates a distinct attempt/candidate/evaluation history, and never re-engineers.
- The installed evaluator/fixture bundle is digest-pinned; drift fails closed.
  The staging registry may change only the scoring digest, not cohort or budget.
- Approval recovers the correction run from the exact prepared evaluation selection;
  it does not accidentally re-aggregate original results or choose the latest run.
- Staging regression demonstrates original pass -> actual retained baseline regrade
  fail -> independently approved superseding snapshot, retaining the old publication.
  The initial pass is seeded fixture evidence, NOT a real benchmark score.
- Web pages expose prepare/review, correction runs, withdrawal and exports using
  generated API types. Single-maintainer review is explicitly not independent.

## Verified so far (final for this session)

- Staging publish → regrade → supersede regression on PostgreSQL: PASS.
- Full `tests.test_api_service` module: every test that completed, passed
  (including all ENG-017/018 and ENG-011 regressions) in multi-module runs.
- `tests.test_worker_leasing`: every test that reached a verdict passed, both on
  the shared `aieb_test` database and a fresh isolated database. The ERROR
  cascade seen in one run was traced to two unittest processes sharing one
  database; after serialization, previously-erroring tests pass individually
  (e.g. the retry/divergence pair in 7.698s, the reconciler/claim pair in 7.422s).
  A single uninterrupted full-class run was not completed in this session: one
  attempt was stopped by me, and the isolated database was dropped externally
  mid-run (shared-container interference, confirmed by
  `FATAL: database "aieb_prompt14_check" does not exist` after prior success).
- `tests.test_attempt_lifecycle` and `tests.test_analysis`: passed in earlier
  multi-module runs (17 analysis tests).
- `tests.test_accounting_and_cli`: 6 tests PASS (earlier run).
- Frontend production build: PASS. `--maxWorkers=2` frontend suite: 53/53 PASS
  across 15 files (75.49s), accessibility included, no relaxed assertions.
- `generate_openapi.py --check` and pinned `openapi-typescript@7.13.0 --check`:
  PASS; `apps/web/src/api/schema.ts` byte-identical to the declared client artifact.


## Open acceptance gates / limitations

ENG-018 remains IN_PROGRESS, not COMPLETE. ENG-011 round-2 human acceptance remains
explicitly open; implementation authorization does not silently close that gate.
No real public release or production deployment was performed or authorized.
Regrade is restricted to local/test/staging and the installed trusted scoring bundle;
arbitrary external evaluator registries are not supported.
Preparation reads now exist (closed on `prompt-14-gap-closure`, PR #3):
`GET /v1/publications/preparations/{preparation_id}` serves the exact prepared
snapshot, evidence manifest, correction reason, and identity-aware approval
eligibility after recomputing both digests, and the review UI loads it through
an "Inspect prepared materials" panel. No identity endpoint was added: the
server remains the sole identity authority, and local self-approval blocking
still covers only IDs prepared in that page session. Ephemeral signing keys
are not durable production keys.

## Gap-closure verification (2026-09-17, branch `prompt-14-gap-closure`, PR #3)

- PostgreSQL regressions (migrated `aieb_gap_review` database): preparation-
  detail read (401 unauthenticated; reviewer eligible; preparer blocked with
  reason; digests and manifest present), saved-draft retrieval assertion, and
  the historical invalidity review regression — all PASS.
- Targeted frontend tests: PublicationReview 13, CampaignAdmin 7,
  CampaignProgress 4 (24 total) PASS; `tsc --noEmit` clean; production build
  PASS.
- Full frontend suite: 55/56 PASS; the one failure was the accessibility
  route sweep exceeding its default 5s timeout, which passes in isolation
  (5.2s) and now carries an explicit 30s test budget. No axe rules relaxed
  and no assertions weakened.
- `generate_openapi.py --check` and pinned `openapi-typescript@7.13.0 --check`
  PASS; `apps/web/src/api/schema.ts` byte-identical to the declared client.
- Earlier full backend discovery was terminated locally and was inconclusive.
  The uninterrupted rerun below now closes that follow-up; it does not close
  the separate human-acceptance gates.
Remote CI and independent acceptance must be recorded separately; local checks do
not imply a green Ubuntu workflow.

## Recorded remote CI (2026-09-17)

For main commit `7953e229a1397ad0fb0dfdf3856eddd630183eba`, the GitHub API
reported these completed successful runs:

- [ENG-015 lifecycle and migration gates, attempt 1](https://github.com/HaisamAbbas/AI-Engineer-Bench/actions/runs/35245006962).
- [API artifact staleness](https://github.com/HaisamAbbas/AI-Engineer-Bench/actions/runs/35245006999).
- [Task admission](https://github.com/HaisamAbbas/AI-Engineer-Bench/actions/runs/35245006983).

The ENG-015 workflow runs six named backend modules: attempt lifecycle,
candidate artifacts, worker leasing, API service, API migrations, and HTTP
integration. It also runs the website build/unit tests and browser sweep.
**This is not full backend discovery** and does not include
`tests.test_invalidity_review`. Earlier conversation claims equating this
workflow with uninterrupted full discovery were incorrect.

The earlier PR-head run `35243824831` failed while waiting for Chrome's
DevTools `/json/version` endpoint; its second attempt completed successfully.
That startup timeout was not a completed browser assertion failure. These
remote results do not constitute ENG-011 human acceptance or independent
review-console acceptance.

## Uninterrupted full backend discovery (2026-09-17)

- Tested commit: `a1e3d7c58b67ca1ff069bf305358162484b0be26`. Its entire committed
  tree is identical to main merge `7953e229a1397ad0fb0dfdf3856eddd630183eba`
  (`git diff --quiet` returned 0). Only this evidence document was edited
  during the run; no source, tests, or workflow files changed.
- Windows, existing virtual environment, `AIEB_ENV=test`, migrated disposable
  PostgreSQL database `aieb_gap_review` on localhost:5544. One discovery
  process ran to completion without interruption or competing discovery.
- Command from the repository root:
  `.venv\Scripts\python.exe -m unittest discover -s tests -p test_*.py -v`.
- **188 tests run in 546.495 seconds: 187 passed, 1 skipped, 0 failures,
  0 errors; process exit code 0.** Includes the new invalidity-review module.
- Skip: `integration.test_eng001_harbor.HarborCompatibilityTest.test_timeout_collection_separate_replay_and_cleanup`
  requires the explicit `AIEB_RUN_HARBOR_INTEGRATION=1` opt-in. This run does
  not claim Harbor/Docker compatibility acceptance.
- Previously reported Windows-sensitive engineering-child-death and
  task-admission cleanup tests both passed in this run. Their historical
  failures remain recorded in `../../PROMPT14_HANDOFF.md`; no test was
  removed, weakened, or rerun separately to obtain this full-run result.
- Local raw log: `D:\AI-Engineer-Bench\.cache\prompt14-final-discover.log`.
  SHA-256: `4b7c02528b24e9b0db8630e2a92833005c461ecc9ca2fd51f0d7ef6cb08cfccb`.
  Exit-code file: `D:\AI-Engineer-Bench\.cache\prompt14-final-discover.exit`.
  These local cache files are not committed artifacts.

Full-discovery and remote-CI recording follow-ups are now satisfied for the
recorded tree. ENG-011 round-2 human acceptance and independent review-console
acceptance remain open; ENG-017/018 are not promoted to COMPLETE by this run.

## Prompt 14 review fixes and coverage disclosure (working tree, 2026-09-17)

The following results concern the uncommitted review-fix tree, not the historical
commits above. They do not establish remote CI or human acceptance for this tree.

- Campaign start now keeps reservation, enqueue, state, and replay response in
  the caller-owned transaction under a campaign row lock.
- Dispatch is state-gated; completion uses authoritative selected evaluations;
  cancellation retains the reservation until work drains.
- Revision pins fail closed on ambiguous slugs. Preview exposes the exact ordered
  frozen trial matrix. Reservation estimates include repetitions and replacements.
- Backend focused batch: 120 tests passed in 331.444 seconds, exit 0.
- Full discovery BEFORE the coverage-disclosure change: 198 tests in 505.170
  seconds, 197 passed, one Harbor/Docker opt-in skip, zero failures/errors, exit 0.
  Log: `D:\AI-Engineer-Bench\.cache\prompt14-blocker-discover.log`.
  SHA-256: `f41a2d387297d6a4577ff9ffac55139cc19f0781db8b4bcfacc4c07b28d127e0`.
  This is not a full-discovery claim for the later coverage-disclosure change.

### Additional test-first disclosure fix

A new publication preparation regression first failed because the snapshot lacked
`coverage_disclosure` (one failure, exit 1). Aggregation now records per-attempt
trace presence/missingness and per-role reported/estimated/unknown receipt coverage,
including physical retries, without exposing request identities or event payloads.
The disclosure is included before snapshot hashing. Trace presence does not assert
complete action instrumentation; missing accounting does not imply zero spend.
Hard-cost eligibility remains false: complete provider accounting and hard budget
enforcement are not established by this path.

The first API-module run exposed two regressions: the strict public snapshot schema
rejected the added field. Typed disclosure models and an optional historical-snapshot
field fixed that mismatch without relaxing extra-field rejection. The new regression,
publication lifecycle, and retained-candidate regrade/supersede journey then all
passed: three tests in 8.906 seconds, exit 0.

OpenAPI and pinned TypeScript regeneration/checks passed; both generated clients
are byte-identical. TypeScript compilation and production build passed after the
schema fix. Final API module: **81 tests passed in 191.782 seconds, exit 0**.
Log: `D:\AI-Engineer-Bench\.cache\prompt14-coverage-api-final.log`.
SHA-256: `997c81c77ff87a357996e9a6da815fa22ebc6acb1ca5004bd7d8e64905ba4209`.
Frontend: **56 tests / 15 files passed in 30.33 seconds, exit 0**.
Log: `D:\AI-Engineer-Bench\.cache\prompt14-coverage-web.log`.
Non-failing Starlette deprecation and psycopg connection ResourceWarnings remain.
No source or test files changed during either final suite run.

### Still open — Prompt 14 is NOT fully satisfied

This list is historical (as of the date above). Two items below were superseded by
the later nine-point review closure in `review-closure.md` (2026-09-18) — kept here
for the record, not because they remain open:

- ~~Browser authentication wiring: the access-token provider setter has no application
  caller; backend token verification alone is not a usable authenticated UI journey.~~
  Superseded: `review-closure.md` item 8 — real authorization-code + S256 PKCE flow,
  server-derived roles, real-browser viewer/operator checks passing.
- ~~Mutation replay across the remaining write operations and network-loss retries.~~
  Superseded: `review-closure.md` item 1 — all 12 persisting API operations now use
  principal-scoped replay records sharing a transaction with the business write.
- Publication eligibility enforcement for protocol-required cost/trace coverage,
  and complete pinned analysis/provenance metadata. Disclosure alone does not close this.
  Still open: `required_trace_coverage` protocols fail closed unconditionally (see
  `review-closure.md` item 2) — no trace-completeness contract exists yet.
- Public error/reason handling review and authenticated staging browser acceptance.
  Addressed for browser acceptance: `review-closure.md` items 8-9. Public error/reason
  handling for corrections is addressed by item 7; broader public error-handling review
  remains open.
- Separate human-review gates, Harbor opt-in compatibility, and current-tree remote CI.
  Still open — see `review-closure.md`'s "Remaining external acceptance" section.

No production deployment or real public release was performed.
