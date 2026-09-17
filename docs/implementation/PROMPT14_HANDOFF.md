# Prompt 14 (ENG-017 + ENG-018) — resume handoff for Codex

Date: 2026-09-17. Branch: `prompt-14-eng017-eng018` (one PR, backend-first then UI; staging/fixture
publish only — no real public publication). Approved plan:
`C:\Users\haisam.abbas\.claude\plans\snuggly-toasting-yeti.md` (read it — it has the full design).

## Environment / how to test
- Disposable PostgreSQL runs in Docker container `aieb-test-postgres` on `localhost:5544`.
  `AIEB_DATABASE_URL=postgresql+psycopg://postgres:aieb_test_password@localhost:5544/aieb_test`,
  `AIEB_ENV=test`. Migration head is `f5a2c1d9e7b4` (already applied to that DB).
- Run API tests: `python -m unittest tests.test_api_service` (Windows venv `.venv\Scripts\python.exe`).
  Worker: `tests.test_worker_leasing`, `tests.test_attempt_lifecycle`. Analysis: `tests.test_analysis`.
- Commit author must be `Haisam Abbas <HaisamAbbas@outlook.com>`; end messages with
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## DONE and COMMITTED (9acad14)
- Migration `services/api/src/aieb_api/migrations/versions/f5a2c1d9e7b4_admin_publication_and_corrections.py`
  (down_revision `e304c7d58a21`): `campaign.created_by_user_id`; `budget_reservation`;
  `correction_run` + `evaluation.correction_run_id`; `publication_preparation`; publication signing
  columns (`manifest_signature`, `signing_public_key`, `signing_key_id`, `signed_manifest`,
  `review_kind`, `reason`); publication immutability trigger extended to freeze the signed manifest
  while still allowing `status`/`reason` transitions. Round-trips clean (downgrade/upgrade verified).
- `models.py`: matching rows (`BudgetReservationRow`, `CorrectionRunRow`, `PublicationPreparationRow`,
  new `PublicationRow`/`CampaignRow`/`EvaluationRow` columns).
- `schemas.py`: ALL Prompt-14 schemas already defined (ENG-017 + ENG-018), incl.
  `MatrixPreview`, `CampaignStateResponse`, `CampaignProgress`, `InvalidAttemptEntry`,
  `PublicationPrepareRequest`, `PublicationPreparationSummary`, `PublicationReviewRequest`,
  `PublicationWithdrawRequest`, `RegradeRequest`, `CorrectionRunSummary`, `PublicationSignature`,
  `PublicationExport`.
- `budgets.py`: `reserve_campaign_budget`, `set_reservation_status`, `reserved_amount_from_resolved`,
  `reservation_summary`.
- ENG-017 routes in `routes/campaigns.py`: preview, start (reserve+enqueue), pause/resume, cancel,
  GET detail, GET progress, GET invalid-attempts. `worker/repository.py`: `claim_work_item` excludes
  PAUSED campaigns only (cancelling stays claimable so it drains to cancelled);
  `maybe_complete_campaign` (running→completed/incomplete, marks reservation consumed), wired in
  `worker/loop.py`. 13 ENG-017 tests in `tests/test_api_service.py`, all green; full
  `test_api_service` + `test_worker_leasing` (30) green.

## DONE but UNCOMMITTED (in working tree — commit with the ENG-018 slice)
- `services/api/src/aieb_api/signing.py` — Ed25519 sign/verify (`sign_manifest`, `verify_manifest`,
  `generate_signing_key_pem`). Key from `AIEB_PUBLICATION_SIGNING_KEY` PEM, else ephemeral per-process.
  Verified working (valid passes, tampered fails).
- `services/api/src/aieb_api/routes/authorized.py` (MODIFIED) — extracted two reusable helpers:
  `verified_published_manifest(publication)` and
  `public_run_evidence_for(session, trial, publication, selection)` (the whitelist redaction core,
  now shared). `get_public_trial` calls `public_run_evidence_for`. App builds; existing public-trial
  test must still pass — RUN `test_api_service` after committing.
- `services/api/src/aieb_api/publication_export.py` — `build_publication_export(session, pub_id)`
  returns `PublicationExport` (redacted; reuses `public_run_evidence_for` + `_frozen_manifest_data`).

## NEXT STEPS (in order)

### 1. `routes/publications.py` (NEW) + register in `app.py`
Endpoints:
- `POST /v1/campaigns/{campaign_id}/publications/prepare` (role `operator`), body
  `PublicationPrepareRequest`. Require `campaign.state in ('completed','incomplete')` else `conflict`.
  Run pinned analysis: `snapshot = aggregation.aggregate_campaign_snapshot(session, campaign_id)`
  (catch `CampaignNotAggregatable` → `conflict`/`invalid_request`). Compute
  `selected_evaluations` (see helper below), `manifest = build_evidence_manifest(session,
  campaign_id, selected_evaluations, snapshot=snapshot)`. Insert `PublicationPreparationRow`
  (status `prepared`, snapshot+digest via `snapshots.snapshot_digest`, evidence_manifest + its
  `evidence_integrity.evidence_digest`, `prepared_by_user_id`=current user,
  `supersedes_publication_id`/`correction_reason` from body). Idempotent: if a `prepared` prep with
  same campaign+both digests exists, return it. Return `PublicationPreparationSummary`.
- `POST /v1/publications/preparations/{prep_id}/review` (role `reviewer`,`administrator`), body
  `PublicationReviewRequest{decision, review_kind, notes}`. Require prep.status=='prepared'.
  Record a `ReviewRow(target_type='publication_preparation', decision=...)`. On `reject`: set
  prep.status='rejected'; return. On `approve` — **PUB-01 self-approval rejection**: `forbidden` if
  reviewer == `prep.prepared_by_user_id` OR reviewer == `campaign.created_by_user_id`. Build the
  canonical manifest `{schema_version:'aieb.publication-signed-manifest/v1', campaign_id,
  snapshot_digest, evidence_manifest_digest, reviewer_id, review_kind, supersedes_id, created_at}`
  (created_at = now isoformat), `signed = signing.sign_manifest(canonical)`. Atomically INSERT the
  immutable `PublicationRow` (status `published`, snapshot+snapshot_digest, evidence_manifest,
  reviewer_id, review_kind, manifest_signature/signing_public_key/signing_key_id/signed_manifest,
  supersedes_id=prep.supersedes_publication_id, reason=prep.correction_reason). If superseding, set
  the prior publication `status='superseded'`. Set prep.status='published',
  prep.published_publication_id. `AuditEventRow(action='publication_published')`. Return summary.
  NOTE: both `review_kind` values require not-self; the label is honest disclosure only (single_
  maintainer is NOT equivalent to independent) — surface it publicly.
- `POST /v1/publications/{pub_id}/withdraw` (role `reviewer`,`administrator`), body
  `PublicationWithdrawRequest{reason}`. published/superseded → `withdrawn` (idempotent if already
  withdrawn), set `reason`, `AuditEventRow(action='publication_withdrawn')`. Snapshot retained
  (results.py already serves a withdrawn notice).
- `GET /v1/publications/{pub_id}/export` (public) → `publication_export.build_publication_export`.
- `GET /v1/publications/{pub_id}/signature` (public) → `PublicationSignature` or 404 if unsigned.

Helper `_selected_evaluations(session, campaign_id, correction_run_id=None) -> dict[trial_id, eval_id]`:
for each trial, pick the scored evaluation of a terminal, non-invalid attempt where
`evaluation.verdict is not None`. Prefer `correction_run_id`-matching evals when given, else the
ORIGINAL (`correction_run_id IS NULL`). `build_evidence_manifest` re-validates each pinned eval.

App registration: `app.py` includes routers — add `from .routes import publications` and
`app.include_router(publications.router)` next to the others.

### 2. Tests (append to `tests/test_api_service.py`; reuse `_create_and_freeze`, `_drive_campaign_to_terminal`)
Prepare→review→publish happy path (a completed campaign with one scored trial → prepared → approve
by a DIFFERENT reviewer subject → published, signature verifies via `signing.verify_manifest`);
self-approval rejected (approver == creator/preparer → 403); reject path; withdraw retains snapshot
+ `/v1/publications/{id}/results` still 200 with notice; export has NO private keys (assert absent:
candidate source/diffs, engineering_stdout/stderr, diagnostics, cost_usd non-null, artifact ids,
non-whitelisted trace keys); signature tamper → verify False; duplicate prepare returns same prep;
publish of an un-aggregatable campaign (frozen-matrix mismatch) → conflict. Note the seeded budget
in `_registry_payload` has all `limit_usd: None`; use `_create_and_freeze(budget_limits="1.5")` for
concrete reservation amounts. Watch the shared-subject role trap: `_auth_header(("reviewer",),
subject="reviewer-only")` so the reviewer is NOT the operator/creator (default subject
"test-subject" gets operator from `_create_and_freeze`).

### 3. Regrade (full re-evaluation) — its own commit
CRITICAL DESIGN NOTE: `build_evidence_manifest` enforces
`attempt.terminal_status == evaluation.verdict` AND terminal_status in the scored set (this is the
ENG-016 invariant). A regrade evaluation may have a DIFFERENT verdict than the original attempt's
terminal_status, so you CANNOT pin a corrected evaluation onto the original attempt. Resolve by
having a regrade create a NEW attempt on the trial that reuses the retained candidate's
`stored_candidate` bytes: new `AttemptRow(number=max+1, phase='terminal', terminal_status=<corrected
verdict>)`, new `CandidateRow` (same tree/manifest digest + stored_candidate; uq is (attempt_id,
tree_digest) so no collision), new `EvaluationRow(correction_run_id=<run>, schedule_digest folds in
`correction_run.scoring_correction_digest` so it is a distinct identity)`. History is preserved
(original attempt/candidate/evaluation untouched). Then:
- `POST /v1/campaigns/{id}/regrade` (role `reviewer`/`administrator`), body `RegradeRequest{registry,
  reason}`: create `correction_run`; enqueue a `regrade` work item per retained valid candidate
  (`WorkItemRow.type='regrade'` — no CHECK on type). The worker re-scores via
  `execute_leased_verification`'s persisted-candidate path parameterized with the corrected
  evaluator/fixture; on record, create the new attempt+candidate+evaluation as above. Reuse
  `worker/runner_bridge.py` and `worker/repository.py` (add a `regrade` dispatch in
  `execute_leased_work`). Mark `correction_run.status='completed'` when its items drain.
- Extend `aggregation.campaign_observations(session, campaign_id, correction_run_id=None)` to prefer
  the corrected attempt/evaluation for regraded trials when a `correction_run_id` is given (keep the
  `_validated_campaign_trials` membership guard intact — it checks TRIALS, not attempts, so extra
  attempts are fine). Then a superseding publication is `prepare(correction_run_id=...)` →
  review/approve with `supersedes_publication_id` set.
- Tests in `tests/test_worker_leasing.py` (regrade work item drains; paused exclusion already
  tested) and `test_api_service.py` (regrade → corrected superseding snapshot; originals unmutated;
  correction visible in `/v1/corrections`).

### 4. Web UI (last) — `apps/web`
Regenerate `openapi.json` + `schema.ts` (`scripts/generate_openapi.py`, `generate_typescript_client.py`;
this ALSO fixes the pre-existing "API artifact staleness" red — the committed `api-client.d.ts` is
stale). Add mutation hooks in `src/api/hooks.ts` and pages `CampaignAdmin.tsx`,
`CampaignProgress.tsx`, `PublicationReview.tsx` (+ `.test.tsx` each). Actions reflect authoritative
server state; show cancellation consequences; never render a frozen plan as editable; self-approval
blocked in UI too; single-maintainer honest label shown.

### 5. Ledger + evidence + PR
Update `STATUS.md` (ENG-017/018 → IN_PROGRESS/COMPLETE as earned), `DECISIONS.md`,
`SESSION_HANDOFF.md`; add `docs/implementation/evidence/ENG-017/` and `ENG-018/`. Verify:
`generate_openapi.py --check`, `generate_typescript_client.py --check`, `apps/web` build+test, full
Python suites on the disposable PG. Push branch; open PR; let ENG-015 Ubuntu workflow +
api-artifacts run. Delete this handoff file and `docs/implementation/PROMPT14_HANDOFF.md` is not
needed after merge (leave `diff_review.txt` untouched — it is intentionally untracked).

## Verification (2026-09-17, full suite on disposable PG)
- `python -m unittest discover -s tests` on a freshly recreated `aieb-test-postgres`
  (migration head `f5a2c1d9e7b4`): **185/187 passed**, 1 skipped. `tests.test_api_service`
  (80 tests) green in isolation. No ENG-017/ENG-018 regressions.
- Two failures, BOTH environmental to this Windows box — NOT branch regressions (do not chase):
  1. `test_worker_leasing.test_death_during_engineering_real_subprocess_kill_is_replaced_and_orphans_removed`
     — asserts a killed child left the item `leased`, but gets `ready`. Deterministic here
     because the child subprocess cold-start (import SQLAlchemy + 3 packages + `db.configure()`)
     exceeds the test's hard-coded `time.sleep(2)` window, so `process.kill()` lands before the
     claim commits. Confirmed PRE-EXISTING: reproduces identically on `main` (merge-base c76c9be)
     against a clean main-migrated DB. Fix (separate, not P14): widen/poll the startup window.
  2. `test_eng013_admission.test_new_task_admission_controls_and_resets` — `PermissionError:
     [WinError 32]` during `shutil.rmtree` of `.cache\tool.session-isolation-admission\...`.
     A Windows file-lock-on-cleanup race under concurrent runs; PASSES clean in isolation.
- Environment gotcha for future runs: stray leftover `python.exe` unittest processes contending
  on the test DB cause exit-255 startup crashes with no test output. Kill strays and, if needed,
  recreate the container before a run.

## Open human-review gates (record honestly)
- ENG-011 round-2 aggregation acceptance (validated via Codex; committed `2b6c2a9`).
- Independent vs single-maintainer review is a disclosed LABEL; real public publication is
  out of scope/unauthorized.
- Budget reservations are ESTIMATED (no provider hard hold exists).
