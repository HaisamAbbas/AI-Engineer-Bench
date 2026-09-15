# Session handoff

Updated: 2026-09-15
Current phase: Post-Prompt-12 audit remediation — original 17-finding review fully triaged (AUDIT-001/002/003); two further independent reviews (AUDIT-004, AUDIT-005) found 6 more real defects, all fixed; ENG-011 and ENG-015 are now IN_PROGRESS (not COMPLETE), pending a decision on ENG-015's leasing-granularity disagreement and end-to-end wiring of ENG-011's planned_cells/category inputs

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

## Recommended next prompt

Get a decision on the ENG-015 leasing-granularity question above before treating ENG-015 as
settled, and decide whether ENG-011's `planned_cells`/`category` inputs should now be wired end to
end from a real campaign (currently only tests supply them). Once those are resolved, Prompt 13
(ENG-016, the public website and compare views) is next, now that ENG-011's analysis package
(including the corrected per-entrant/per-category outputs) and ENG-014's registry/results API
(plus its new typed TypeScript client) exist to serve it. Separately, independent task reviews
remain a precondition before any admitted-only ENG-013 release manifest can be created; ENG-012
remains blocked on provider/model
authorization, credentials, and spend cap; ENG-017 (admin campaigns, budget reservations,
`POST /campaigns/{id}/start`) is the next hosted-API dependency now that ENG-015's leasing layer
exists for it to dispatch onto.
