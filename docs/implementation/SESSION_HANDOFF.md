# Session handoff

Updated: 2026-09-14
Current phase: Prompt 11 — ENG-014 complete (hosted metadata API, auth, migrations)

## Current state

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

## Recommended next prompt

Prompt 12 (ENG-015): PostgreSQL worker leasing and crash recovery against the `work_item`/
`attempt` tables ENG-014 already persists. Separately, independent task reviews remain a
precondition before any admitted-only ENG-013 release manifest can be created; ENG-012 remains
blocked on provider/model authorization, credentials, and spend cap.
