# Session handoff

Updated: 2026-09-16
Current phase: Prompt 13 (ENG-016, public website) implemented; a second independent review found six more real gaps (no CORS, untyped responses, no comparison eligibility, incomplete results table, wrong pagination ordering, thin test coverage), all fixed; a third review then found three of those fixes (comparison eligibility, results-table completeness, publication provenance) only partially correct plus new gaps in Compare/downloads/encoding, all fixed (ENG016-007/008/009) - ENG-016 remains IN_PROGRESS (run evidence/methodology/task-ticket-text gaps remain, disclosed, plus the third review's finding #5 - real HTTP/browser integration tests - deliberately deferred); post-Prompt-12 audit remediation complete across five independent review rounds (AUDIT-001-007); ENG-015's leasing split (ENG015-007) is implemented, then a further review (ENG015-008) found and fixed four more real gaps (verification cancellation, stored-candidate digest checking, legacy-row handling, idempotent artifact-first writes) plus narrowed one topology overclaim - COMPLETE; ENG-011 remains IN_PROGRESS

## Current state

An independent review spanning Prompts 01-12 raised 17 findings across ENG-002/003/006/009/011/
013/014/015. Eleven were verified as real and fixed:

AUDIT-001 (four findings): (1) `aieb task validate` never actually validated `TaskRevision` -
five of twelve task.yaml files had genuine schema defects (unquoted digest strings parsed as
integers, invalid `category`/`egress_policy` values) that passed CLI validation anyway; the CLI
now validates for real and all five task.yaml files were corrected; (2) hosted auth trusted a
bearer token's `aieb_roles` claim directly instead of consulting the persisted `role_bindings`
table - `resolve_roles` is now the sole authorization source, and a related bug (artifact
ownership comparing an OIDC subject string against an internal UUID, which could never match)
was fixed alongside it; (3) worker cancellation was checked only once, at claim time - a cancel
arriving mid-run now interrupts the attempt via a second background poll thread; (4)
`aieb_analysis`'s `summarize()` could not detect a wholly-missing planned task/entrant cell, only
an under-repeated existing one - fixed via an optional `planned_cells` parameter.

AUDIT-002 (three more findings): (5) a Windows drive-qualified path (`C:/outside`) passed every
existing path-safety check (`_safe_relative`, `CandidateFile.path`, `SubmissionPolicy`
include/protected) since none of them start with `/` or contain `..`, yet `Path(destination) /
"C:/outside"` discards `destination` entirely on Windows - fixed at all three call sites, plus
`safe_extract_tar` now verifies every resolved target stays under its destination root; (6) any
bare `RuntimeError` from an evaluator was blamed on the candidate - fixed via a dedicated
`CandidateUnavailableError` the harnesses raise explicitly instead; (7) nothing below the API
route layer stopped a direct `UPDATE` against frozen task/evaluator/entrant/fixture revisions or
a frozen campaign's manifest - fixed via Postgres `BEFORE UPDATE` triggers, verified to block a
raw `psql` UPDATE outside the ORM entirely while still permitting legitimate campaign state
transitions.

All seven are covered by tests against the real code/database paths (several verified to
actually fail against the pre-fix code, not just pass against the fix), and the full regression
suite (83 tests across core/CLI/API/worker/analysis/artifacts) still passes.

AUDIT-003 (four more findings, completing the triage): (8) every task.yaml's `repository_digest`/
`provenance_digest`/`contract_digest`/`service_topology_digest`/`evaluator_digest` was a
repeated-digit placeholder that never reflected any real content - `scripts/compute_task_digests.py`
now derives each from the actual repo/provenance.json/contract/environment-doc/evaluator source on
disk, and `aieb task validate` recomputes and rejects a mismatch instead of only checking the hex
shape (`official_image`'s digest stays a placeholder - no image has ever been built or pushed, so
there is nothing real to hash); (9) TOOL-02's fixture always returned success immediately, so its
ambiguity check never exercised real ambiguity - the fixture now commits the write and drops the
acknowledgment on the first attempt per identity, and all four backend variants were corrected to
genuinely retry (or fail) under that real uncertainty; (10) `aieb_analysis.summarize()` emitted only
one combined suite-wide rate and folded/omitted verifier cost - it now also reports `per_entrant`,
`per_category`, and a standalone `verifier_cost_total_usd`; (11) nothing recomputed a publication's
`snapshot_digest` against its stored snapshot before serving it - fixed the same way as AUDIT-002's
revision immutability, via a `BEFORE UPDATE` trigger plus an application-level recompute-and-compare
on every read, returning 503 on a mismatch instead of serving corrupted results.

The remaining six findings were verified and closed without a code change, because the concern
turned out to already be true or already honestly stated: publication-endpoint status/auth gating
(re-verified during AUDIT-002: not currently exploitable, only public-forever statuses exist yet);
verification not being an independently leased work-item type (already an accepted ENG015-001
design decision predating this review); whether "twelve distinct family IDs" is genuine diversity
(independently confirmed: each task's backend.py tests a genuinely different bug/domain, though
several are very thin inside a shared generic HTTP harness - documented as-is, not overclaimed);
the catalog's "validated" label (already correctly scoped by `review_status:
pending-independent-review`, and now that `aieb task validate` is a real check, an accurate claim);
ENG-012's pilot preparation (already labeled `prepared-not-authorized`/BLOCKED, never presented as
resolved); and the worker's dependency on `tests.maintainer.*`/`suites/dev` as its evaluator
registry (real, but a production registry is a design question for ENG-019's official isolation
work, not something to invent speculatively now).

ENG-015 was completed in Prompt 12 before this review. `aieb_api/worker/` (repository, runner_bridge, loop, reconciler, metrics)
adds atomic work acquisition (`SELECT ... FOR UPDATE SKIP LOCKED` + `UPDATE ... RETURNING`),
generation/fencing on every state transition, heartbeat/expiry, artifact-first finalization
(the candidate/evaluation row commits before the work item is marked done, so a crash between
the two leaves durable evidence), reconciliation (resumes from an already-recorded candidate
rather than replacing when one exists; otherwise replaces the attempt up to the frozen
campaign's `max_replacements`), cancellation (`cancel_campaign`/`is_campaign_cancelling`), and
orphan teardown of local `engineer`/`build` allocations a killed worker's own cleanup never
reached. `LocalAttemptRunner` (aieb-runner, unchanged in scoring logic) gained one
backward-compatible optional `cancel_event` parameter so a worker can cooperatively interrupt
an in-flight attempt, not just stop dispatching new ones. `tests/test_worker_leasing.py` (8
tests) covers every controlled-failure scenario the prompt names against the same real
disposable Postgres container ENG-014 uses — including a real OS subprocess that is genuinely
killed mid-engineering to prove orphan teardown against actual leftover files, not simulated
state. `enqueue_frozen_campaign` is an internal capability (not yet wired to any HTTP route);
`POST /campaigns/{id}/start` with budget reservations remains ENG-017. Local CLI functionality
is untouched. No remote deployment occurred.

ENG-014 is complete. `services/api` (new workspace member `aieb-api`) persists exactly the
tables spec section 30 names — task/evaluator/fixture revision, suite release/task, entrant
revision, campaign, trial, attempt, work_item, candidate, evaluation, artifact/ref,
usage_request/receipt, publication, review, audit_event — plus `idempotency_record` for API-01,
with uniqueness/FK/check constraints enforced in PostgreSQL itself. Two Alembic migrations exist
(initial schema, then an additive `campaign.submitter_note` column demonstrating the expand
phase); upgrade/downgrade/upgrade was run against a real disposable Postgres container, not
sqlite. FastAPI implements registry/results reads (`/v1/tasks/{slug}/revisions/{version}`,
`/v1/entrants/{id}`, `/v1/releases`, `/v1/publications/{id}/results`, `/v1/comparisons`,
`/v1/trials/{id}`, `/v1/artifacts/{ref}/download`) and campaign draft/freeze
(`POST /v1/campaigns`, `PATCH /v1/campaigns/{id}`, `POST /v1/campaigns/{id}/freeze`, the last via
the existing `aieb_core.planner.freeze_campaign`). OIDC auth fails closed when unconfigured;
`TestIdentityProvider` refuses to construct outside `AIEB_ENV=test`. `tests/test_api_service.py`
(12 tests) runs against a real test Postgres and covers API-01, API-02, stale If-Match edits
(412), invalid state transitions (409), corrupt/unresolvable manifests, and idempotent replay;
`tests/test_api_auth.py` (4 tests) covers fail-closed behavior without a database. Local CLI
functionality is untouched — no `packages/aieb-*` code changed. Campaign
start/pause/resume/cancel, reviews, and publication writes remain ENG-017/ENG-018; worker
leasing against `work_item` rows remains ENG-015; a TypeScript client from the checked-in
`docs/implementation/evidence/ENG-014/openapi.json` is deferred until `apps/web` exists
(ENG-016). No remote deployment occurred.

ENG-010 is complete locally. EXT-02 (`ext.batch-alignment`) is a runnable HTTP extraction application with a deliberately broken positional mapping baseline. Its evaluator uses shuffled output, partial failure, and last-occurrence-wins repeated IDs to verify correspondence and retention. TOOL-01 (`tool.false-completion`) is a runnable HTTP workflow application with a deliberately dishonest completion baseline. Its evaluator owns an independent operation service and ledger; candidate logs do not decide outcomes. Both task families have public API/requirements, visible data, baseline/reference/alternative/shortcuts, provenance, maintainer-only fixtures, ten-reset controls, and pass through the CLI fresh replay path.

ENG-011 is complete locally. `aieb-analysis` is the authoritative metric implementation for per-task s/n, Wilson intervals, repeatability, complete-plan eligibility, cost-per-resolution unknown/zero-success behavior, time/deadline/attrition metrics, and project/family limitations.

ENG-012 is prepared but blocked. `examples/development-pilot-18.json` freezes an offline 3 task x 2 deterministic fixture entrant x 3 repetition matrix. It was not executed as a real pilot because no provider/cloud authorization, credentials, approved cap, or fixed real-agent configuration exists. It is not presented as a campaign result.

ENG-013 authoring now has twelve public runnable fixture applications. RAG-02/03/04, EXT-01/03/04, TOOL-02/03/04 have complete local baseline/reference/alternative/shortcut controls and ten fresh local reference resets, including TOOL-02's complete post-fix matrix. `suites/dev/catalog.json` records twelve distinct synthetic family IDs, exceeding the six-project development diversity floor; `aieb` has an explicit trusted evaluator/runtime mapping for each task; task-admission CI is in `.github/workflows/task-admission.yml`. Structured admission evidence for the nine newly authored tasks is recorded in `docs/implementation/evidence/ENG-013/admission-report.json`. These are local validation results, not official admission. Independent review remains pending for every task, so no admitted-only frozen suite release manifest is valid yet.

No real agent, paid provider, remote deployment, publication, or push occurred in this phase.
ENG-014 added local hosted-API service code and its own real-Postgres test evidence, but nothing
was deployed remotely. Existing independent-review, ENG-001 real-agent, and ENG-019
official-isolation gates remain pending/blocked.

## Commands

```powershell
uv sync --all-packages --locked
.\.venv\Scripts\python.exe scripts\run_ext02_admission.py
.\.venv\Scripts\python.exe scripts\run_tool01_admission.py
.\.venv\Scripts\aieb.exe --json run --campaign examples\ext02-local-campaign.json
.\.venv\Scripts\aieb.exe --json run --campaign examples\tool01-local-campaign.json
.\.venv\Scripts\python.exe -m unittest tests.test_analysis tests.test_ext_tool_admission -v

# ENG-014 (requires a real disposable PostgreSQL instance; see docs/implementation/evidence/ENG-014/api-service.md)
docker run -d --name aieb-test-postgres -e POSTGRES_PASSWORD=aieb_test_password -e POSTGRES_DB=aieb_test -p 5544:5432 postgres:16
$env:AIEB_DATABASE_URL = "postgresql+psycopg://postgres:aieb_test_password@localhost:5544/aieb_test"
cd services/api; ..\..\.venv\Scripts\python.exe -m alembic upgrade head; cd ..\..
$env:AIEB_ENV = "test"
.\.venv\Scripts\python.exe -m unittest tests.test_api_auth tests.test_api_service -v
.\.venv\Scripts\python.exe scripts\generate_openapi.py

# ENG-015 (same test Postgres instance; see docs/implementation/evidence/ENG-015/worker-service.md)
.\.venv\Scripts\python.exe -m unittest tests.test_worker_leasing tests.test_attempt_lifecycle -v
```

## Continuing working rules

- Implement only the requested phase and its necessary prerequisites.
- Inspect before editing.
- Tests must exercise behavior.
- Preserve immutable benchmark evidence.
- Never invent scores or present mocks as live evaluations.
- Never weaken hidden-evaluator isolation, scoring, or reproducibility to make a demo pass.
- Local reversible work should proceed without repeated confirmations.
- Use already-authorized budgets only.
- Track blocked gates honestly.
- Do not automatically commit, push, deploy, or publish unless authorized.
- When commits are requested, use separate role-specific commits rather than bundling unrelated phases.

## AUDIT-004 (second independent review, of the AUDIT-001/002/003 remediation itself)

A second review checked the remediation work rather than the original code, and found five more
real defects, all fixed: `resolve_roles` ignored `role_bindings.scope` entirely (a role bound to
one campaign/suite/test silently satisfied any global check) - fixed via a `GLOBAL_SCOPE`
sentinel and a `scope` parameter; `per_category` (added in AUDIT-003) blended every entrant's
rate into one number per category - fixed to `{category: {entrant: rate}}`; `evaluator_digest`
(added in AUDIT-003) hashed only `evaluator.py`, missing `tests/maintainer/common.py` (11 of 12
evaluators) and rag01's sibling `fixture.py` - fixed via `hash_evaluator_closure`; the checked-in
TOOL-02 admission evidence was stale (pre-fix behavior) - regenerated via a new script; and
Prompt 11's typed TypeScript client artifact, previously deferred to ENG-016, is now generated
(`docs/implementation/evidence/ENG-014/api-client.d.ts`), superseding ENG014-004.

One finding is a genuine architecture disagreement, deliberately left open rather than resolved
unilaterally: whether ENG-015 satisfies Prompt 12 without verification being its own independently
leased/heartbeated/fenced PostgreSQL work item. ENG015-001 (2026-09-14) already weighed this same
prompt tension (leased execution *and* verification, vs. reusing the local pipeline as one call)
and chose the latter, disclosing the gap; the new review argues the former should have won. Whoever
directs this work next should decide whether to invest in splitting the leasing phases (a
substantial, invasive change to an already fully-tested system) or continue accepting the disclosed
gap - see DECISIONS.md AUDIT-004 for the full argument on both sides.

## AUDIT-005 (third independent review, reproduced a failure rather than only reading code)

Found and fixed one more real correctness bug: `complete_for_rank` counted raw observations per
cell against `required_repetitions`, not valid/resolved ones - a cell with the right number of
raw attempts but an infrastructure-invalid one among them (not a scored outcome) reported
complete with a smaller `n` instead of incomplete. Reproduced directly (a 3-observation cell with
one `execution_valid=False` reported `complete_for_rank: True`, `n=2`, against
`required_repetitions=3`), fixed by deriving completeness from `per_task`'s own valid-observation
count, and verified to fail against the pre-fix code before restoring the fix. Also flagged, and
fixed: `scripts/generate_typescript_client.py` ran an unpinned `npx openapi-typescript`, so a
future run could silently produce different output - pinned to `openapi-typescript@7.13.0`, and
both generator scripts gained a `--check` mode wired into a new CI workflow
(`.github/workflows/api-artifacts.yml`) so staleness is caught automatically.

The review also argued that AUDIT-004 documenting the ENG-015 disagreement as open didn't itself
satisfy Prompt 12's acceptance gate - agreed on that bookkeeping point specifically (not a new
technical argument): **ENG-011 and ENG-015 are now `IN_PROGRESS` in STATUS.md, not `COMPLETE`**.
The underlying ENG-015 architecture question is exactly as open as AUDIT-004 left it.

## AUDIT-006 (fourth independent review, again reproduced a failure directly)

Found and fixed one more real cost-accounting bug: `cost_per_resolution`'s numerator summed every
observation's engineer+dev-application cost regardless of `execution_valid`, while the denominator
was scored successes only - a single expensive infrastructure-invalid attempt inflated the
per-resolution figure despite never being a scored outcome. Reproduced directly ($3 valid success
plus a $200 infrastructure-invalid attempt reported `203.0`, not `3.0`), fixed by scoping the
numerator (and its missing-accounting check) to `execution_valid` observations, matching the
denominator; added a new `total_campaign_cost_usd` field (every attempt, valid or invalid) for the
spec's separate "publish total campaign cost including invalid attempts" requirement, which
previously had no output at all. Verified to fail against the pre-fix code before restoring the
fix. Also fixed: the CI workflow's PR path filter didn't include the two generated artifacts
themselves, so a PR hand-editing one directly (bypassing the generator) would skip the staleness
check meant to catch exactly that - both paths added to the trigger. The review's other two points
(ENG-015 leasing, ENG-011's unwired planned_cells/category) are the same already-open items from
AUDIT-004/005, correctly re-confirmed rather than newly found.

## AUDIT-007 (fifth independent review, found AUDIT-006's own fix incomplete)

Two more real gaps in the AUDIT-006 fix itself, both fixed. First, AUDIT-006 scoped the cost
numerator to `execution_valid` observations, but that isn't quite "scored" - an execution-valid
observation can still have `passed=None` (an unresolved/indeterminate evaluation awaiting a
verdict), and `per_task`'s own scored definition is `execution_valid AND passed is not None`.
Reproduced directly ($3 scored success plus a $200 execution-valid-but-unresolved observation
reported `203.0`, not `3.0`) and fixed by introducing that exact `scored` population for the cost
numerator. Second, `total_campaign_cost_usd` only summed engineer + dev-application cost,
excluding verifier cost despite its name - fixed to be a genuine grand total of all three
(engineer + dev-application + verifier); there is still no infrastructure-cost field on
`TrialObservation`, so the total doesn't include one yet, a disclosed gap rather than a silent one.

## Prompt 13 (ENG-016, public website) - implemented

`apps/web` (React 19/TypeScript/Vite) implements every read-only route Prompt 13 names against
generated API types (`src/api/schema.ts`, from the checked-in OpenAPI schema). Three minimal read
endpoints were added to `services/api` because the website genuinely needed them: `GET /v1/tasks`
(public task catalog), `GET /v1/corrections`, and `supersedes_id` on publication results
(ENG016-001). Disclosed, not fabricated, gaps: public run evidence shows an honest
"requires authorization" state rather than an invented redaction boundary (ENG016-002);
Methodology is real static content (no versioned protocol API exists to back a stub); Compare is
scoped to one publication's cohort (cross-release comparison needs a two-publication-ID endpoint
that doesn't exist). 11 frontend tests (Vitest + Testing Library) cover empty/error/withdrawn
states, null-sorts-last, the 4-entrant cap, URL-backed filters, malicious-content escaping, and
structural accessibility (axe-core); MSW was tried for network-level mocking but did not reliably
intercept fetch under this environment's jsdom+Node25 combination, so tests mock the typed API
client directly instead (still real hook/component code). See
`docs/implementation/evidence/ENG-016/website.md` for full detail. Not done: real-browser visual/
responsive inspection (no visual browser tooling available here).

## Prompt 13 second-pass review - six more gaps fixed

A second independent review found the first pass didn't actually satisfy Prompt 13's acceptance
criteria and reproduced each finding directly: (1) no CORS middleware - a real preflight was
blocked; fixed with `AIEB_CORS_ALLOWED_ORIGINS` (ENG016-003). (2) `/v1/publications/{id}/results`
and `/v1/comparisons` returned bare `dict`; replaced with real typed responses, which surfaced a
genuine separate bug - snapshot digesting used `aieb_core.canonical.content_hash`, which forbids
floats that real analysis output legitimately contains; fixed with a dedicated float-tolerant
`snapshots.py` (ENG016-004). (3) comparison eligibility (UI-02) was never implemented - now checks
real `cohort_digest` compatibility and returns real per-task paired differences, and cross-release
comparison is a reachable UI path via `entrant_publication_ids`, not just a documented gap
(ENG016-005). (4) the results table was missing most spec'd columns - added resolved-task estimate,
valid trials, per-category rates. (5) releases/corrections were ordered oldest-first, breaking
"latest publication" selection once paginated - reproduced directly, fixed to newest-first, and
real pagination controls added where none existed (ENG016-006). (6) frontend tests grew from 11 to
26, covering every page and several states that had no coverage at all (Compare's paired
differences, EntrantProfile's results-by-release, ReleaseDetail's superseded state, error-recovery
Retry). Full regression: 115 Python tests, 26 frontend tests, clean `tsc`/`vite build`. Remaining
disclosed gaps: run evidence's redaction boundary, task ticket text (`instruction.md`), candidate
logs/diffs, and real-browser visual inspection - see `docs/implementation/evidence/ENG-016/
website.md`.

## Prompt 13 third-pass review - three second-pass fixes were only partial, plus three new gaps

A third independent review found findings #2, #3, and #7 from the second-pass review only partially
addressed, plus three more real gaps. All fixed except the review's own finding #5, deliberately
deferred as a separate effort - see DECISIONS.md ENG016-007/008/009 for full detail:

1. **Cross-release comparison still violated spec journey 6.1** ("never a calculated winner" -
   unconditional). The second pass allowed paired differences across different publications
   whenever `campaign.cohort_digest` matched - but `Cohort` doesn't carry the exact task list,
   entrant revisions, or repetition plan, so a matching digest never proved matching observations.
   Fixed: cross-publication comparisons are now unconditionally non-comparable; paired differences
   only exist within a single publication (ENG016-007).
2. **Per-entrant cost/time/deadline/attrition were still suite-wide numbers**, and resolved-task/
   valid-trial counts were recomputed in the browser. Fixed at the source:
   `aieb_analysis.metrics.summarize()` gained real per-entrant breakdowns for all of these (mirroring
   how `per_entrant`/`per_category` already group by entrant) plus `required_repetitions` for a real
   "all-k" label; `Results.tsx` now reads all of it directly, no client-side derivation
   (ENG016-008).
3. **Publication provenance could be misrepresented**: `ReleaseDetail.tsx` inferred the frozen task
   list from which tasks had a snapshot cell (a zero-observation task vanished); entrant profile
   results always showed whichever revision is newest now, even for old historical results. Fixed:
   `frozen_tasks`/`cohort` now come from `campaign.resolved` (the real frozen manifest), and each
   entrant result row now carries the EXACT `entrant_version` that publication's campaign actually
   used (ENG016-008).
4. **Compare showed only an aggregate rate** - fixed with a real per-entrant panel (version, model,
   capabilities, cost, time, coverage), not merely "Rate: X" (ENG016-009).
5. **Downloaded Results JSON omitted publication ID, snapshot digest, cohort/protocol identity** -
   fixed to download the full response bundle, not just `snapshot` (ENG016-009).
6. **A mojibake `Â·` between Home's two links** - fixed with a JS unicode escape immune to any
   file/transport encoding layer (ENG016-009).

Not attempted: the review's finding #5 (real HTTP/browser integration tests exercising actual
`fetch`/CORS/a running backend, and full-page accessibility automation beyond Home/TaskCatalog) - a
distinct, substantially larger effort, disclosed as open in `docs/implementation/evidence/ENG-016/
website.md` rather than bundled into this pass.

Full regression after this pass: 125 Python tests (1 skipped, against real Postgres - includes 15
`aieb_analysis` tests and 39 `test_api_service.py` tests, up from 13/36 respectively), 27 frontend
tests, `tsc --noEmit` and `vite build` clean. Also corrected: a prior "115 Python tests passing"
claim did not name that PostgreSQL-dependent test classes are skipped (not failed) without
`AIEB_DATABASE_URL` configured - stated explicitly now (125 discovered either way; 64 pass/61 skip
without the database, 124 pass/1 skip with it).

## ENG-015 leasing split - implemented (ENG015-007)

Per direct instruction, ENG015-007 supersedes ENG015-001 and is now implemented: verification is
its own independently-leased PostgreSQL work item (own lease generation/heartbeat/fencing/
finalization), with the candidate persisted before a verification work item is enqueued, reusing
the existing evaluator pipeline unchanged. `aieb_runner.lifecycle.LocalAttemptRunner.run()` split
into `run_engineering()`/`run_verification()` (composed by `run()` for the unchanged local-CLI
path); `candidate.stored_candidate` (new JSONB column, migration `a3f0c9d17b2e`) persists the full
candidate so verification can reconstruct it from the database alone, in a different process if
needed; `repository.py` replaced `record_outcome` with `record_candidate`/`advance_to_verification`/
`record_evaluation`/`load_stored_candidate`; `reconcile_expired_leases` recovers each work-item type
from its own artifact-first evidence, gaining `advanced`/`requeued` counters. All 5 required
controlled-failure scenarios (worker death after candidate persistence, verification lease expiry
with/without a recorded evaluation, stale-verifier fencing, duplicate verification completion) pass
against real Postgres - see DECISIONS.md ENG015-007 and `docs/implementation/evidence/ENG-015/
worker-service.md` for full detail. `worker/loop.py` needed no changes at all: `claim_work_item`
claims across both queues by default, so the existing claim→execute loop already services both
phases. ENG-015 is back to `COMPLETE` in STATUS.md.

## ENG-015 leasing split hardened - four more gaps fixed (ENG015-008)

A further independent review of the ENG015-007 split found four real defects and one topology
overclaim, all addressed - see DECISIONS.md ENG015-008 for full detail:

1. **Verification cancellation was silently dropped.** `execute_leased_work` only ever forwarded
   `cancel_event` to the engineering path; `run_verification()` had no cancellation mechanism at
   all. Fixed: `run_verification()` checks `cancel_event` at the BUILD/VERIFY phase boundary;
   `execute_leased_verification` now runs the same background cancellation-poll thread engineering
   already had, and finalizes a cancelled outcome as `terminal_status="cancelled"` without scoring.
2. **The persisted candidate was trusted without checking its own digests.** Fixed: the
   deserialized candidate's `manifest.full_tree_hash`/`.digest()` are now recomputed and compared
   against `CandidateRow.tree_digest`/`manifest_digest` before BUILD runs; a mismatch routes to
   `infrastructure_invalid` rather than evaluating under a falsified identity.
3. **A legacy/malformed `stored_candidate` (`{}}`) crashed the worker with a bare `KeyError`.**
   Fixed: a typed `StoredCandidateUnavailableError` is raised and caught, routing to
   `infrastructure_invalid` the same way a genuinely-missing candidate row already did.
4. **`record_candidate`/`record_evaluation` were not idempotent.** A retry after an ambiguous
   commit hit their own unique constraints and raised `IntegrityError` instead of returning the
   already-recorded identity. Fixed: both catch that error and return the existing row.
5. **"A different worker"/"the database alone" overstated cross-host independence** - true only
   when workers share a filesystem for `AIEB_WORKER_WORK_ROOT` (no distributed object store
   exists). Narrowed explicitly in docs rather than building one (out of scope); the misleading
   independence test that reused one in-process store/runner between phases was rewritten to
   round-trip through real JSON (de)serialization and a second, independent store instance.

`tests/test_worker_leasing.py` grew from 14 to 19 tests; `tests/test_attempt_lifecycle.py`'s
two-phase independence test was corrected as described above (still 9 tests, all passing). Full
regression: 120 Python tests (1 skipped), against real Postgres. ENG-015 remains `COMPLETE`.

## Recommended next prompt

Decide whether ENG-011's `planned_cells`/`category` inputs should be wired end to end from a real
campaign (currently only tests supply them) - the one remaining IN_PROGRESS item. Prompt 14
(ENG-017/018: admin campaigns, budget reservations, publication/correction workflows) is the
natural next prompt, since it both depends on and will exercise ENG-015's now-complete leasing
layer, and gives the website's Corrections/admin-adjacent pages real write paths to react to.
Independent task reviews remain a
precondition before any admitted-only ENG-013 release manifest can be created; ENG-012 remains
blocked on provider/model authorization, credentials, and spend cap.
