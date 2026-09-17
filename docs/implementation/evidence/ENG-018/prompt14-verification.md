# ENG-018 — Prompt 14 staging implementation

Date: 2026-09-17. Branch: `prompt-14-eng017-eng018`.

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
The UI has no preparation-read or identity endpoint: reviewers must obtain prepared
materials out of band; local self-approval blocking covers IDs prepared in that page
session, while the server enforces actual user identity. This is not a complete
identity-aware review console. Ephemeral signing keys are not durable production keys.
Remote CI and independent acceptance must be recorded separately; local checks do
not imply a green Ubuntu workflow.
