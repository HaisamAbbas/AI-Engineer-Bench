# Session handoff

Updated: 2026-09-20
Prompt 14 is COMPLETE at the engineering-ticket level. ENG-011's aggregation prerequisite and ENG-017/018 were accepted by an independent technical review after the committed PostgreSQL review/API batch passed 92/92 and the complete recorded discovery passed 223 tests with one optional Harbor skip. This is not a claim of separate-human or organizational approval. Production OIDC, two-human official-publication approval, hardened isolation, deployment CI, and public release remain later-ticket or operational gates. No real public release was made. Older phase claims below are historical.

## Prompt 15 continuation pass (2026-09-19/20) - codex-audit gaps 1, 2, 3 implemented

A codex audit of the Prompt-15 closure found six substantive gaps the closure's own tests had
missed. Three are now implemented and tested (the six findings' disposition is ledgered in
DECISIONS.md ENG019-004/ENG020-005 and this handoff):

1. **Raw-socket/L3 egress bypass unverifiable** -> the effective-network-policy guard now REfuses:
   `HarborBackend.launch()` computes the trial's effective phase network via Harbor's OWN resolver
   (`resolve_trial_network_plan` + verifier-mode resolution, post `extra_allowed_hosts` merge) and
   refuses before any Docker/Harbor call when the effective mode is `PUBLIC` (including the
   declared-vs-default case) or an ALLOWLIST phase names a denied metadata host
   (`_task_network_offence`, `packages/aieb-runner/src/aieb_runner/backends/harbor/backend.py`).
   Decision: an undeclared network mode is fail-closed refusal, a mount-offence takes the error
   slot first (kept ordering).
2. **Docker-socket mount scan bypassable by Compose interpolation / include / extends / YAML tags**
   -> `_find_unauthorized_host_mount` is fail-closed: resolves `${VAR}`/`$VAR` + `:-`/`-`/`:?`/`?`/`:+`/`+`/`$$`
   against sibling `.env` then process env, follows `include:` and `extends.file:` graphs, refuses
   unknown-tag/unparsable compose files ("cannot be safely inspected", `!override`/`!merge`/`!reset`),
   refuses residual-`$` and required-but-unset bind sources. Proven against a real specimen in this
   repo's own `.cache/research/harbor-v0.22.0/.../clbench/task-template/environment/docker-compose.yaml`
   (`${CONTEXT_DIR}/messages` - previously silently skipped). 39/39 green in
   `tests/test_eng019_sandbox_threat_model.py`; the old
   `test_launch_does_not_refuse_when_hardened_isolation_is_not_required` was re-based to a bare temp
   dir because the repo ROOT is genuinely non-compliant any more (by design).
3. **No per-attempt scoped credentials / no candidate-verifier identity separation (spec §37)**
   -> implemented end to end: `attempt_credential` table + Alembic migration
   (`ba47e9c84511_attempt_scoped_credentials.py`), one live sha256-hashed credential per
   `(attempt_id, actor_role)` with expiry+revocation, repository
   `issue/verify_attempt_credential`/`revoke_attempt_credential`/`attempt_credential_status`,
   `POST /v1/attempts/{attempt_id}/credentials/verify` (authenticated BY the presented credential,
   deliberately NOT operator-authenticated - that separation is the mechanism),
   `EngineeringCommand.extra_env` (candidate) and `run_verification(attempt_vars=...)` (verifier)
   delivering `AIEB_ATTEMPT_ID/ROLE/CREDENTIAL` into the subprocess environments, worker phase
   executors in `runner_bridge.py` issuing at phase start and revoking in `finally`. Non-PG
   behavior proven: `tests/test_attempt_lifecycle.py` 18/18 (incl. two new env-delivery tests).

A codex follow-up review of gap 3 returned six findings (fourth review round, disposition in
DECISIONS.md ENG019-005/ENG020-006 and `sandbox-review.md`), then a SECOND review round
(ENG019-006/ENG020-007) found four of those closures incomplete and one import-time env leak; the
reviewer's own negative run re-opened that leak as a HIGH blocker (`runner_bridge` imported the
evaluator module in the WORKER PARENT). All are now closed: (1) issuance is attempt- and
role-fenced (work-item type -> allowed role, `engineering` -> candidate / `verification`+`regrade`
-> verifier; cross-attempt or type mismatch refused); (2) revoke is fenced to the credential row's
OWN lease identity - a stale worker's delayed `finally` NO-OPS against a replacement's rotated
token; (3) the reconciler's lease sweep covers crashed `regrade` items and revokes both roles; (4)
`GET /v1/attempts/{attempt_id}/candidate` is verifier-ROLE-ONLY (`_require_role`: 401 absent/
invalid, 403 valid-but-wrong-role) returning the FULL stored payload, and verification issues its
verifier credential first then reads the candidate through the SAME credential gate
(`load_stored_candidate_authorized`, `CredentialDeniedError` -> infrastructure_invalid), revoking
on every infra-abort path; (5) evaluators are delivered as `(module, qualname)` identity STRINGS -
never the pickled callable - and resolved only after `os.environ` is scrubbed (allowlisted-field
credential stripping included); (6) the worker parent no longer imports the evaluator at all:
identity (incl. qualname) lives IN `TASK_RUNTIMES` and threads straight to the spawned child,
which is the FIRST process to import the module, after the scrub. Negative controls plant worker
secrets BEFORE any probe import and drive the real production path (identity strings; end-to-end
leased execution via `execute_leased_work`), asserting the probe never enters the worker parent's
`sys.modules` and its import-time snapshot saw no secrets - asserted red, then reverted, against
the reintroduced parent import. Gap 3 is CLOSED - the reviewer's deciding run on `895814a` accepted it for the currently
supported architecture (hosted worker imports no evaluator modules in its parent; evaluator
identity is plain `(module, qualname)` strings; the isolated child is the only importer, after the
scrub; production-path parent-import control and all other negative controls pass; credential +
worker-leasing 53/53, lifecycle + sandbox 60/60, working tree and whitespace clean; official
VM/live-infrastructure validation explicitly deferred). Suites green against the disposable
`aieb-test-postgres` container (postgres:16 on `localhost:5544`,
`postgresql+psycopg://postgres:aieb_test_password@...`): credentials 14/14, lifecycle 21/21,
worker leasing 39/39, sandbox threat-model 39/39. Single alembic head `bc5e9d4b2107`.

Gap 4 (restore-drill pre-reconciliation fencing hole) is IMPLEMENTED as a system fence epoch
(monotonic `system_fence.lease_fence_epoch`, migration `d5a3f7b9c1e2`, committed and pushed in
`f1000a6`): every fenced lease/credential operation now requires its stored epoch stamp to equal
the current epoch, and the reconciler sweeps stale-epoch leases even when in-window. The deciding
review's findings 1 and 2 were fixed and pushed in `27b14be` (fence row held FOR SHARE for each
fenced transaction via `current_fence_epoch(..., share_lock=True)` so the exclusive advance cannot
interleave; and `attempt_credential_status` `valid` now requires `row.lease_epoch == current`).
RE-REVIEW ROUND 2 on `27b14be` then found the operator command's kill-switch barrier check was
NOT atomic with the epoch bump (BLOCKING: a concurrent deactivation could land after the check and
after `advance_fence_epoch` had committed, so the command reported `advanced=False` while the
epoch had moved and automation could retry-advance repeatedly), plus two mediums (the claimed
"operator-DB-role authenticated" was not implemented - no operator role/grants/current_user
validation; and the drill bypassed the command, calling `repository.advance_fence_epoch()` directly,
which is why the non-atomicity escaped it). All three are FIXED in locally-staged, uncommitted
changes (regression-tested; negative controls reverted to green): `advance_fence_epoch_with_barrier`
makes the barrier and bump ONE transaction (lock `kill_switch` FOR UPDATE, confirm ACTIVE, bump the
fence epoch under its own lock, commit once - a concurrent deactivation either completes first and
causes REFUSAL with no epoch change, or blocks until the advance commits; both orderings
two-session regression-tested, worker-leasing 44 + credentials 15 green); the command now reports
`current_user` and `--by-user` is re-scoped to an OPTIONAL, INFORMATIONAL audit label (executable
`--check`/refusal/advance verified against the control plane); and the drill now drives
`scripts/fence_advance.py` AS A SUBPROCESS (restored DB inherits the backup's INACTIVE kill switch;
`--check` fails then passes; refusal leaves the epoch unchanged; advance only under a
RE-ESTABLISHED barrier; resume precedes the fresh-claim assertion) with the runbook rewritten to
that exact order. RE-REVIEW OF THIS ROUND IS PENDING (fixes staged uncommitted on `27b14be`);
re-review of `27b14be` itself confirmed HEAD == origin/main there and untouched tracked files. gap 5
(operator kill-switch API/CLI - the repository knob exists, the operator surface does not) and gap 6
(Prometheus `/metrics` + alerting) have NOT started; gap 5 is next after gap 4 accepts. PG-gated
execution is no longer blocked here: the `aieb-test-postgres` container
(postgres:16, port 5544, `aieb_test_password`) is what the recent runs of the PG suites
(credential 10/10) used; the other existing ENG-015/016/017/020 PG suites (worker leasing, API
service, migrations, drills) can be run the same way going forward.


Current phase: ENG-011 and ENG-015 through ENG-018 are COMPLETE. See `evidence/ENG-011/aggregation-review.md` and `evidence/ENG-018/review-closure.md`. STATUS.md's backlog table is authoritative; later official-release prerequisites remain blocked under their own tickets.

ENG-016 is COMPLETE. Two further independent-review passes were remediated and accepted (see DECISIONS.md ENG016-017): seven findings on the public-evidence path (strict included/excluded published-selection union; snapshot-digest binding of the evidence manifest - swap-proof after publication, with semantic rate-derivation verification explicitly deferred to ENG-018; terminal/scored/completed-campaign publication gating; whitelist-redacted public trace; immutable-evaluation-only public usage; lease-fenced `phase.started`; keyboard-operable ARIA tabs), then one follow-up blocker (terminal_status must be a real scored verdict AND equal the pinned evaluation's verdict). Committed and pushed at `beb4bb4`.

ENG-015 is COMPLETE. Its gates had never actually executed on Ubuntu: the CI workflows pinned `astral-sh/setup-uv` to a SHA that no longer resolves, so every run failed at "Set up job". Repinning to a valid release (v10.1.0) let the Unix gates run for the first time, which then demonstrated three more failures, each fixed narrowly: (1) an out-of-sync `apps/web` npm lockfile (regenerated under node 22); (2) two Unix-only failures - `test_api_migrations` hardcoded `.venv/Scripts/python.exe` (now `sys.executable`) and VERIFY concluded "no result" before draining the tail of a large result still buffered in the Unix socketpair after the writer exited (now drains to completion; verified in a Linux container); (3) the browser-seed step colliding with rows the regression step leaves in the shared database (now resets the schema before seeding). The ENG-015 workflow is green on commit `10bb063`. Separately, the "API artifact staleness" workflow is red on a PRE-EXISTING stale `api-client.d.ts` (its `openapi.json` matches the current API; the ENG-016 manifest models are internal validation shapes, not route models, so they never entered the OpenAPI) - out of scope for these tickets and left for whoever regenerates that artifact.

ENG-011 is COMPLETE. Its aggregation-integration gate is closed: `services/api/src/aieb_api/aggregation.py::aggregate_campaign_snapshot` derives a frozen campaign's OWN planned (task, entrant) cells and per-task categories from `campaign.resolved` and wires them through `summarize(planned_cells=...)`/per-observation category, building the snapshot from persisted trials/attempts/evaluations and role-split usage receipts. A wholly-missing planned cell is now detected as incomplete coverage in real aggregation. ENG-018's publication flow consumes this function. With ENG-011/ENG-015/ENG-016 all COMPLETE, Prompt 14 (ENG-017 + ENG-018) is unblocked.

Prior context (unchanged): ENG-016's earlier five review findings were implemented and exercised against PostgreSQL, the frontend test/build gates, and real Edge browser checks; the previous false mobile report was replaced with a 26-case report that asserts both requested and visual viewport widths.

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

## Prompt 13 fourth-pass review - four of the third pass's fixes were still partially overstated

A review of the third pass found four of its own claims (findings #2, #3, #4, #6 in that review's
own numbering) still partially overstated, plus a real correctness bug - see DECISIONS.md
ENG016-010 through ENG016-012 for full detail:

1. **Per-entrant coverage still excluded zero-observation frozen tasks.** `per_entrant_total_tasks`
   counted DISTINCT OBSERVED tasks per entrant (`aieb_analysis.metrics.summarize()` has no access to
   the frozen plan), so a two-task campaign with one unobserved task reported "1/1" instead of
   "1/2." Fixed at the API layer: `GET /v1/publications/{id}/results` now overrides it with
   `len(frozen_tasks)` for every entrant (every entrant is scheduled against every frozen task by
   construction). Separately, all `per_entrant_*` fields' schema default changed from `{}` to
   `None`, so a snapshot predating these fields reports honestly unavailable, not a fabricated `0`
   via the frontend's old `?? 0` fallback.
2. **Compare still showed the newest entrant configuration, not the publication's exact one.**
   Every panel called `GET /entrants/by-slug/{slug}` (always the newest revision). Fixed with a new
   `GET /v1/publications/{publication_id}/entrants/{slug}`, reading `campaign.resolved["entrants"]`
   directly - pinned to that publication forever, unaffected by a later revision being registered.
3. **Median engineering time was wrong for an even sample.** `times[len(times)//2]` picked the
   upper-middle value (`[10, 20]` -> `20`, not `15`), and a test had locked the defect in as expected
   behavior. Fixed with `statistics.median`; both `successful_engineering_median_seconds` and
   `per_entrant_median_engineering_seconds` are now `float`.
4. **Provenance remained incomplete and partly mislabeled.** `CohortIdentity` gained
   `budget_profile_id`/`application_model_profile`/`required_capabilities`; `hardware_class` is no
   longer mislabeled "(profile)"; `created_at` is labeled "Published," not "Evaluation date"; a
   genuine `evaluation_started_at`/`evaluation_completed_at` window (derived from real
   `attempt.created_at` timestamps already in the database) and `protocol_scoring_digest` were added
   to the response and the downloaded bundle.
5. **Aggregate rate deltas were still labeled "paired task outcomes."** Renamed throughout -
   `paired_differences`/`TaskPairedDifference` are now `task_rate_deltas`/`TaskRateDelta`, and the UI
   heading is "Per-task rate deltas" - since the backend already disclosed these aren't
   repetition-matched pairing, but the naming still implied it.

Not attempted: that review's finding #6 names the same already-disclosed open acceptance gates
(HTTP/browser integration tests, valid/planned trial coverage, per-entrant uncertainty, public
redacted run evidence, versioned Methodology, task ticket text, candidate log/diff rendering,
full-page accessibility/responsive checks) rather than a new claim to fix.

Full regression: 42 `test_api_service.py` tests (up from 39) and 16 `test_analysis.py` tests (up
from 15) pass against real PostgreSQL, 27 frontend tests, `tsc --noEmit`/`vite build` clean.
Environment note: Docker Desktop was found not running partway through this session's verification
(the `aieb-test-postgres` container had stopped along with it) - restarted directly
(`docker start aieb-test-postgres`) rather than working around it, and the full suite was re-run
clean afterward; two stale test runs from the outage window failed with connection timeouts and
were correctly discarded as artifacts of that outage, not real regressions.

## Prompt 13 fifth-pass review - the fourth pass introduced two integrity bugs plus two more gaps

A fifth review found the fourth pass's own fixes for findings #1/#3 introduced integrity bugs, and
that #2/#4 were still incomplete - see DECISIONS.md ENG016-013/014:

1. **The API served a snapshot that no longer matched its digest (High).** The fourth pass's
   frozen-task-coverage fix mutated `snapshot.per_entrant_total_tasks` at read time via
   `_apply_frozen_task_coverage`, then returned that mutated snapshot beside the ORIGINAL
   `snapshot_digest` - so the response and download bundle carried a digest that no longer hashed
   its own snapshot, breaking immutable provenance. Fixed: the snapshot is returned exactly as
   stored (never mutated); the correct frozen-plan total is served via the separate `frozen_tasks`
   list and read client-side as `len(frozen_tasks)`. `test_served_snapshot_still_hashes_to_its_recorded_digest`
   now guards this invariant.
2. **The "evaluation completed" timestamp was fabricated (High).** `_evaluation_date_range` derived
   the window from `min`/`max(AttemptRow.created_at)`, but that column only records row CREATION
   (enqueue), not evaluation finish - a single long attempt reported a zero-duration window. Removed
   both fields entirely rather than serving accurate-sounding fabricated data; a real window needs
   durable lifecycle timestamps that do not exist yet. `protocol_scoring_digest` (real frozen-manifest
   data) stays.
3. **A frozen entrant with zero observations still vanished from the table (Medium).** Rows came from
   `snapshot.per_entrant` only. Fixed: `frozen_entrants` (from `campaign.resolved["entrants"]`) is
   served, and `Results.tsx` builds rows from the union with observed entrants - an unobserved frozen
   entrant shows a genuine 0 valid trials and "Unknown" aggregate (never a fabricated 0%). Count-vs-
   metric null semantics are explicit: a count is `null` only for a legacy snapshot (whole field
   absent) and a genuine `0` when the map is present but the slug unobserved; a rate/cost/time metric
   is `null` in both cases.
4. **Same-slug-across-releases comparison corrupted panels (Medium).** `ComparisonResponse.entrants`
   was a dict keyed by slug, so comparing agent-a@release-1 vs agent-a@release-2 overwrote one entry
   and both panels showed the same aggregate. Fixed: `entrants` is an ordered list of
   `ComparisonEntrantPanel`, one per selection, each carrying its own `publication_id`; the exact
   same `(slug, publication)` twice is rejected with 400. `Compare.tsx` renders from the list and
   removes by position.

Full ENG-016 verification: 46 `test_api_service.py` + 16 `test_analysis.py` tests pass against real
PostgreSQL, 29 frontend tests, `tsc --noEmit`/`vite build` clean.

Heads-up for the next session: `packages/aieb-runner/src/aieb_runner/lifecycle.py` has UNCOMMITTED
local changes that are NOT part of this ENG-016 work - a separate, coherent rework of the ENG-015
cancellation mechanism (the `Evaluator` type now takes a cooperative cancel `Event`, plus a new
`CancelledError` and an `EVALUATOR_CANCEL_GRACE_SECONDS` grace period). It was left untouched and
excluded from the ENG-016 commits. It changes the evaluator signature, so it likely needs matching
updates to every evaluator (`tests/maintainer/*/evaluator.py`) and to `runner_bridge`'s call sites
before the worker/lifecycle tests will pass; whoever owns that change should finish and verify it.

## Prompt 13 sixth-pass review - the fifth pass's digest fix was still incomplete, plus a contract regression

A sixth review found the fifth pass's snapshot-digest fix (ENG016-013) still broke for a legacy
snapshot missing the `per_entrant_*` keys, plus a contract-quality regression in comparison
eligibility - see DECISIONS.md ENG016-015:

1. **A legacy snapshot's served digest broke again, differently (High).** `_verified_snapshot()`
   parses stored JSONB through `AnalysisSnapshot.model_validate()`, whose `per_entrant_*` fields
   default to `None` when a legacy snapshot lacks those keys entirely. Ordinary response
   serialization dumps every declared field including those filled-in defaults, so a legacy snapshot
   was served with extra `null` keys its stored/digested JSON never had - reproduced directly by
   deleting a key from a seeded snapshot and recomputing the digest from the HTTP response, which no
   longer matched `snapshot_digest`. Fixed with `response_model_exclude_unset=True` on the results
   route: FastAPI/Pydantic's exclude-unset dump uses each nested model's own `model_fields_set`, so a
   legacy snapshot's absent keys stay genuinely absent in the response, while a current-format
   snapshot (every key present) is unaffected.
   `test_served_snapshot_still_hashes_to_its_recorded_digest_for_a_legacy_snapshot` covers this
   directly; `test_per_entrant_total_tasks_is_null_not_zero_for_a_legacy_snapshot` was updated to
   assert the key is genuinely absent (not present-and-null).
2. **Comparison eligibility had regressed to an unconstrained flat shape (Low).** When
   `publication_id` was added to the panel (fifth pass), `ComparisonEntrantPanel` became a single
   model with both `aggregate` and `reason` optional - nothing in the contract stopped an
   eligible=True panel carrying a `reason`, or an eligible=False panel carrying a fabricated
   `aggregate`. Fixed with a real tagged union (`EligibleEntrantPanel`/`IneligibleEntrantPanel`,
   `Literal[True]`/`Literal[False]`). Deliberately NOT a Pydantic `Field(discriminator="eligible")`:
   OpenAPI discriminator mappings need string keys, so a boolean-tagged discriminated union
   serializes its mapping with string `"True"`/`"False"` keys, and `openapi-typescript` reads THAT
   for the generated field type instead of the schema's own `const: true`/`const: false` -
   discovered directly while regenerating artifacts: it produced `eligible: "True"` (a string
   literal) in the generated TypeScript even though every real response carries the JSON boolean
   `true`/`false`. Left as a plain (non-discriminated) union instead - Pydantic's smart-union mode
   still disambiguates correctly on the boolean value, and the generated TypeScript now correctly
   types `eligible: true | false`. `test_comparison_entrant_panel_rejects_inconsistent_eligible_reason_combinations`
   covers the validation directly.

OpenAPI/TypeScript artifacts were regenerated (`docs/implementation/evidence/ENG-014/openapi.json`,
`api-client.d.ts`, `apps/web/src/api/schema.ts`). No frontend source changes were needed -
`Compare.tsx` only reads `entry.eligible`/`entry.aggregate`/`entry.reason` by field name, unchanged
by the union restructuring. Full ENG-016 verification: 48 `test_api_service.py` (up from 46) + 16
`test_analysis.py` tests pass against real PostgreSQL, 29 frontend tests, `tsc --noEmit`/`vite build`
clean against the regenerated types.

## Prompt 13 seventh-pass review - the sixth pass's exclude_unset fix was still incomplete, plus a smaller contract gap

A seventh review found ENG016-015's `exclude_unset` fix still broke the served snapshot's digest for
a PRESENT (not absent) stored value, plus one smaller contract gap - see DECISIONS.md ENG016-016:

1. **A legacy snapshot's served digest could still break, for a different reason (High).**
   `exclude_unset` stops a legacy snapshot's genuinely-absent keys from being fabricated back into
   the response, but every key that IS present still round-trips through
   `AnalysisSnapshot.model_validate()` and Pydantic's own schema-driven JSON dump - which coerces a
   stored JSON integer (e.g. `suite_rate: 1`) into a served float (`1.0`) for any `float | None`
   field. Recomputing the digest from that reserialized body no longer matched `snapshot_digest` -
   the same failure mode as ENG016-013's read-time mutation, recurring through Pydantic's own
   (de)serializer rather than application code, reproduced directly with
   `AnalysisSnapshot.model_validate(...).model_dump(exclude_unset=True)` on a snapshot with integer
   rate values. Fixed by no longer letting `response_model` serialization touch the `snapshot` field
   at all: the route now builds its JSON body via `fastapi.encoders.jsonable_encoder` and substitutes
   the ORIGINAL `row.snapshot` dict for the `snapshot` key, returning a `JSONResponse` directly.
   `_verified_snapshot()` still runs first for digest/shape verification (raising 503 on either
   mismatch), but its return value is now used only for that check, never serialized.
   `response_model=PublicationResultsResponse` stays on the route purely for OpenAPI documentation -
   FastAPI does not run a directly-returned `Response` through `response_model` at all -
   and `response_model_exclude_unset` is no longer needed, since the raw substitution alone already
   guarantees a legacy snapshot's absent keys stay absent.
   `test_served_snapshot_preserves_integer_values_that_pydantic_would_coerce_to_float` reproduces the
   coercion directly and confirms both the served values/types and the digest are correct.
2. **`aggregate` was omittable, not required-but-nullable (Low).** `EligibleEntrantPanel.aggregate`
   defaulted to `None`, letting it be dropped from the payload entirely - a weaker contract than
   intended, since the endpoint always computes a real value (possibly itself `None`) for every
   eligible panel. Fixed by dropping the default.

Also fixed: two whitespace-only lines and a lost explanatory comment in `routes/results.py`,
introduced by an editing mistake in the ENG016-015 commit and flagged by the review's
`git diff --check`.

OpenAPI/TypeScript artifacts were regenerated. No frontend source changes were needed. Full ENG-016
verification: 49 `test_api_service.py` (up from 48) + 16 `test_analysis.py` tests pass against real
PostgreSQL, 29 frontend tests, `tsc --noEmit`/`vite build` clean against the regenerated types.

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

## ENG-015 hardened again - a fourth review found ENG015-008's own fixes each only partial (ENG015-009)

A fourth review reproduced a concrete failure for each of ENG015-008's five findings and found four
of the five fixes still incomplete, plus one new gap - see DECISIONS.md ENG015-009 for full detail:

1. **Cancellation still couldn't interrupt an active verification.** ENG015-008 only checked
   `cancel_event` before/after BUILD and VERIFY, never during - reproduced directly with a
   deliberately slow (20-second) evaluator, cancelling 1.5 seconds in. Fixed: `LocalAttemptRunner`
   gained a private `_run_cancelable()` helper that runs BUILD/VERIFY on a background thread polled
   against `cancel_event`; cancellation now returns immediately without waiting for either phase to
   finish (the abandoned thread's eventual result, if any, is simply discarded).
2. **An idempotent evaluation retry could finalize a DIFFERENT verdict than what was persisted.**
   `record_evaluation` returned bare `True` on a retry's `IntegrityError` without comparing the
   stored verdict against the retry's own payload - reproduced with a first "pass" and a conflicting
   second "fail" under the identical identity. Fixed: `record_evaluation` now returns a
   `RecordedEvaluation` - always the AUTHORITATIVE persisted verdict/result - and `runner_bridge.py`
   finalizes from that, never its own local outcome. `record_candidate` gained the same treatment:
   a genuine content mismatch on retry now raises `CandidateConflictError` instead of silently
   returning a different row's id.
3. **Candidate integrity validation covered the manifest but not stored reference metadata.**
   Corrupting a `file_references` entry's id/blob digest/scope still passed the manifest-digest
   check and was misclassified a candidate `CONTRACT_VIOLATION`. Fixed at the classification: any
   `ArtifactError` during verification's BUILD phase is now `infrastructure_invalid`/`HOST_FAILURE`
   - by BUILD, the candidate's own content was already accepted as contract-compliant during
   COLLECT, so a reconstruction failure now is always a storage/reference problem, and
   `reconstruct_candidate()`'s own existing re-verification against the manifest's per-file digest
   already catches this class of corruption.
4. **A malformed nested reference field still escaped the typed error** as a bare `AttributeError`
   (`"id": []`, reproduced directly). Fixed with a typed Pydantic envelope
   (`_StoredCandidateEnvelope`) validating the whole payload via `model_validate`, converting any
   structural/type mismatch to `StoredCandidateUnavailableError`.
5. **The "shared/network filesystem" narrowing from ENG015-008 was accurate but still
   unimplemented by default.** Fixed for real: candidate bytes now live in a new
   `PostgresArtifactStore` (`aieb_api/worker/artifact_store.py`, new `worker_artifact_blob`/
   `worker_artifact_reference` tables, migration `e20d5d09b489`) - the same `AIEB_DATABASE_URL`
   every worker already needs, replacing `FilesystemArtifactStore` as the hosted worker's default.
   Proven with a new test using two completely separate, never-shared local directories for
   engineering and verification - not documentation of a limitation, a passing test with no shared
   filesystem at all. The local CLI is unaffected (no database at all).

`tests/test_worker_leasing.py` grew from 19 to 25 tests. Full regression: all 25
`test_worker_leasing.py`, all 9 `test_attempt_lifecycle.py`, and all 39 `test_api_service.py` tests
pass against real PostgreSQL; the new migration's upgrade/downgrade/upgrade round-trip was verified
directly. ENG-015 remains `COMPLETE`.

## ENG-015 hardened a third time (ENG015-010, baseline for this session's own further fix)

A fifth review found the cancellation fix still only REPORTED cancellation - the abandoned thread
kept executing (subprocesses, network, spend) and finalize could delete the build directory out
from under it - and that the Postgres blob backend violated the frozen ADR-08 with no size bound,
retention class, or orphan expiry. Both were addressed: verification evaluators ran under a
cooperative cancellation contract (a stop Event; an evaluator honouring it was joined via the typed
`CancelledError`; one ignoring it past `EVALUATOR_CANCEL_GRACE_SECONDS` was abandoned, with the build
allocation deliberately left in place for host-side reconciliation instead of deleted); ADR-11
documented the PostgreSQL staging deviation from ADR-08 with a 52 MiB cap, staging/evidence retention
classes, 24-hour `staged_until` expiry, and a reconciler purge job. See DECISIONS.md ENG015-010 for
full detail - this work was already in progress when the session that produced this handoff entry
began, and is treated as the baseline the next review (below) is a review OF.

## ENG-015 hardened a fourth time - ENG015-010's own fixes still didn't close what they claimed to (ENG015-011)

A sixth review reproduced concrete failures for each finding rather than only reading code, and found
ENG015-010 had NOT actually fixed the cancellation race or the containment gap it claimed to, plus
four more real defects - see DECISIONS.md ENG015-011 for full detail:

1. **The cancellation race was never actually closed.** `_run_cancelable()` still returned
   `completed=True` for an evaluator that ignored the stop signal but happened to finish naturally
   DURING the grace window - reproduced directly with a 1.5s-sleeping fixture against the 5s default
   grace. Fixed: `PhaseRun` gained a `cancelled: bool` set the instant cancellation is observed,
   checked unconditionally before `completed` at every call site.
2. **Containment was still theoretical.** A thread can never be preempted, so "abandon" was the only
   option available - not the spec's actual emergency-cancellation requirement to kill active runs.
   Fixed for real: VERIFY's evaluator now runs in an owned, forcibly-killable `multiprocessing`
   (spawn) subprocess (`LocalAttemptRunner._run_verify_isolated`) - `terminate()`, escalating to
   `kill()`, then joined, so the process is CONFIRMED DEAD before the call returns. BUILD (trusted,
   internal `reconstruct_candidate` code) stays thread-based. A related teardown race in that same
   abandon path was fixed too: `reconstruct_candidate` creates `build` only after walking the frozen
   source tree, so an abandoned thread checked at `phase_abandoned and build.exists()` could read as
   "safe to delete" before the directory even existed, then create and write into it AFTER
   finalization had already reported clean cleanup - fixed by deciding preservation on
   `phase_abandoned` alone.
3. **A migration already committed in 47b90e6 had been rewritten in place** to add columns - any
   database that had already applied it would never receive them, while the ORM would expect them
   regardless. Fixed: `e20d5d09b489` restored to its original committed content; a new migration
   (`f2b6c9a417de`) adds the retention/candidate-linkage columns via `ALTER TABLE`.
4. **Orphaned staging artifacts never actually expired.** The purge treated ANY reference (even an
   unclaimed one) as protection, but `collect_candidate` always creates a blob AND a reference
   together - so a real orphan always has exactly such a reference and was never purged in practice;
   the existing test had synthesized an orphan as a referenceless blob, which doesn't reproduce the
   real path. Fixed: since `attach_candidate_references` always flips a blob to `'evidence'` the
   moment ANY reference on it is claimed, a still-`'staging'` blob's references are, by construction,
   all unclaimed - the purge now deletes them before deleting their blob. A related gap: the
   reconciler's engineering-crash recovery path (advance straight to verification when a candidate was
   already persisted) never called `attach_candidate_references` at all - fixed by calling it there
   too.
5. **The size CHECK didn't check real bytes** - only the caller-supplied `byte_length` column against
   the cap, so an INSERT could claim `byte_length=1` while storing far more actual `data`. Fixed with
   `octet_length(data) = byte_length AND octet_length(data) <= MAX_WORKER_ARTIFACT_BYTES`.
6. **Visibility validation wasn't anchored** - `Field(pattern=r"public|restricted")` uses `re.match`
   (start-of-string, not whole-string), so `"public-evil"` validated. Fixed with
   `Literal["public", "restricted"]`.

Full regression after this pass: 106 tests across `test_api_service.py` (49), `test_worker_leasing.py`
(29, up from 27), `test_attempt_lifecycle.py` (12, up from 9), and `test_analysis.py` (16) pass against
real PostgreSQL; `test_ext_tool_admission.py` passes unchanged; the new migration's
upgrade/downgrade/upgrade round-trip was verified against a freshly recreated disposable database (the
existing one had been migrated through the now-reverted in-place edit and could not cleanly prove the
split migration chain). ENG-015 was marked `COMPLETE` at that point; the ENG015-012 review below
reopened it pending the listed verification gates.

## ENG-015 follow-up review - four remaining containment and cleanup gaps addressed (ENG015-012)

The next review found two high-severity VERIFY gaps and two medium data/cleanup gaps. Fixes are in:

1. VERIFY establishes a Unix session/process group before evaluator code runs. On Windows the child
   waits until the parent assigns it to a kill-on-close Job Object. Cancellation and normal completion
   stop the full tree, including candidate servers launched with `subprocess.Popen()`.
   `test_verify_cancellation_kills_evaluator_and_descendant_processes` records and checks both PIDs;
   its Windows PID probe now imports `ctypes` and the regression passes.
2. Evaluator and BUILD results use a private capability channel: a Unix socket pair or a Windows
   anonymous pipe whose write handle is explicitly duplicated only into the child. The parent drains
   results while workers run, validates an 8 MiB bounded JSON envelope, and never unpickles worker
   output. Regression coverage sends a 2 MiB result and checks a 9 MiB result is rejected cleanly.
3. Migration `f2b6c9a417de` now links a reference only when exactly one candidate claims it and the
   stored attempt scope, visibility, blob digest, and byte length match the candidate JSON and
   database rows. Ambiguous or inconsistent references remain unclaimed; their staging blobs get
   `created_at + 24 hours` expirations. The real-Postgres regression covers these mismatch cases.
4. BUILD reconstruction now runs in its own process group/Windows Job Object. Cancellation kills
   it while artifact I/O is stalled and waits for its process tree to stop before cleanup. The
   PostgreSQL artifact store reconstructs a process-local SQLAlchemy engine in the child.

## Latest verification

Against a disposable PostgreSQL 16 database, `alembic upgrade head` succeeded and the relevant backend regression command passed all 109 tests, including migration/backfill, worker, lifecycle, artifact, API, and real socket-level HTTP/CORS coverage. On Windows, the lifecycle cases include descendant process termination, results larger than the OS pipe buffer, oversized-result rejection, and BUILD cancellation.

The website production build succeeded and Vitest passed 30 tests. Headless Microsoft Edge `153.0.4234.32` checked all 13 routes at 1440x1000 and 390x844: 26 checks, zero axe violations, and no horizontal overflow. All 26 route/viewport screenshots plus sample API responses are checked in at `docs/implementation/evidence/ENG-016/browser/`; Home desktop, task mobile, and redacted run mobile were manually inspected. These responses come from a clearly identified synthetic fixture in the disposable test database, not an official benchmark publication.

## Current review gates (2026-09-16)

- ENG-015 remains IN_PROGRESS until `.github/workflows/eng015-verification.yml` passes on Ubuntu with its Unix process-group and PostgreSQL checks.
- ENG-016 remains IN_PROGRESS pending review of the follow-up. Public run selection is pinned to an immutable manifest; Run Evidence exposes trace/actions, usage, configuration, artifacts, and invalid/superseded/partial states; candidate display payloads and versioned task ticket text are integrity-bound; and the task bundle includes its ticket. Real Edge verification covers all 13 routes at 1440px and 390px, with matching `innerWidth`/`visualViewport.width`, no horizontal page overflow, real API requests, and zero axe violations.
- The initial commit and push for this review sequence is `e1a9e9b`; the verified implementation changes after that baseline are still uncommitted for review.
- ENG-012 remains blocked on provider/model authorization, credentials, and a spend cap; independent task reviews remain a precondition for an admitted-only ENG-013 release manifest.
