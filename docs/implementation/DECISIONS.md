# Implementation decisions

This log records implementation choices made while executing the source specifications. It does not amend or replace the unchanged specifications in `docs/specs/`. Changes to scoring, isolation, artifact submission, or reproducibility require a dedicated ADR before implementation.

## BOOT-001 — Use the workspace root as the repository root

- Date: 2026-09-13
- Status: accepted
- Decision: initialize the local Git repository at `D:\AI-Engineer-Bench` because the supplied documents were already placed there and no project or repository existed.
- Consequence: no nested project directory, remote, fork, commit, deployment, or publication was created.

## BOOT-002 — Preserve byte-identical canonical specification copies

- Date: 2026-09-13
- Status: accepted
- Decision: copy the supplied architecture and implementation specification to `docs/specs/` without transforming their encoding or content. Preserve the original workspace attachments and ignore them as repository inputs.
- Evidence: SHA-256 is `1acd59c8be999ae750d864415a5eb41bba441e19e0a499e6984c3b98261b1017` for the architecture and `8f1556ac93e2f38af926923e6d13b33e452e9f969528cfcb89e6cfb7d6a2150c` for the implementation specification. `scripts/dev.py check` verifies these hashes.

## BOOT-003 — Apply the refined implementation layout

- Date: 2026-09-13
- Status: accepted
- Decision: use `infra/` (not architecture draft `infrastructure/`) and include `packages/aieb-analysis` when those components are implemented, because Implementation Specification sections 10–11 explicitly refine the layout. Create only structural roots needed for orientation now. Do not scaffold API, web, or empty Python distributions.
- Consequence: `packages/`, `suites/dev/`, `manifests/releases/`, and `infra/` document ownership without pretending their future components exist. `services/api` and `apps/web` are deferred until their tickets.

## BOOT-004 — Pin only the verified bootstrap Python tools

- Date: 2026-09-13
- Status: provisional
- Decision: use Python 3.12.10 and uv 0.11.27 for repository bootstrap because both were present and exercised locally. Record observed Git, Docker, Compose, and Node versions without treating them as compatibility acceptance. Do not select pnpm while no web package exists.
- Consequence: `.python-version`, `pyproject.toml`, and `toolchain.json` make the bootstrap reproducible. ENG-001 may revise Python only when actual Harbor compatibility evidence requires it. Harbor remains deliberately unpinned until Prompt 02.

## BOOT-005 — Keep the development entrypoint dependency-free

- Date: 2026-09-13
- Status: accepted
- Decision: expose `./dev.ps1 doctor|check|test`, backed by Python standard-library code, rather than creating product packages before their tickets.
- Consequence: bootstrap integrity and environment observations are executable now; the entrypoint performs no network calls, provider calls, evaluation, deployment, or publication.

## Open decisions

| Decision | Owner | Current state |
| --- | --- | --- |
| Real installed-agent entrant/model for final ENG-001 smoke | ENG-001 | Blocked pending explicit provider/model authorization, credentials, and existing cap |
| Harbor public-egress/metadata adversarial validation | ENG-019 | Deferred; application allowlist and verifier no-network paths pass, broader isolation is not claimed |
| Node and pnpm pins | Web implementation preparation | Deferred; no web package exists |
| Official VM provider | ENG-019 | Deferred |
| Provider prices and enforceable caps | ENG-008 | Deferred |
| Task resource budgets | ENG-012 | Deferred until pilot measurement |
| Official sample size | ENG-021 | Deferred until variance and budget evidence |
| Primary live semantic scoring | Task admission | Deferred; deterministic checks remain primary initially |

## ENG001-001 — Pin Harbor 0.22.0 behind an AIEB adapter

- Date: 2026-09-13
- Status: accepted for the local execution foundation; real-agent acceptance remains blocked
- Decision: pin Python 3.12.10, Harbor 0.22.0, and uv-build 0.8.4. The Harbor release resolves to commit `4407eb5227a2ff4f0d3f16b2eb48849382fdf276`; the PyPI wheel SHA-256 is `4c4c6571b3d160ed0cb45b82918136751fb08e7b8596412723ac00dde12eeabb`. Keep Harbor-specific imports behind the AIEB runner backend boundary.
- Evidence: `docs/implementation/evidence/ENG-001/compatibility-report.md` and `deterministic-run-summary.json`.
- Consequence: the deterministic Docker contract supports multi-service editing, deadline collection, separate replay, and scoped teardown. It does not establish real-agent compatibility, complete usage accounting, production isolation, or adversarial egress enforcement.
- Supply-chain note: the release tag is annotated but unsigned. The tag object, commit, wheel hash, and lockfile are all recorded so this limitation is explicit rather than silently trusted.

## ENG002-001 — Canonical JSON is the permanent content-identity boundary

- Date: 2026-09-13
- Status: accepted
- Decision: model versioned core contracts with Pydantic 2.13.5 and generate Draft 2020-12 JSON Schema artifacts. Compute content hashes from UTF-8 canonical JSON with sorted keys, explicit nulls, finite integer values, and normalized decimal strings; do not hash YAML bytes or use floating-point values.
- Consequence: equivalent YAML formatting yields the same digest, while a semantic change yields a new digest. Draft configuration and frozen resolved campaign are distinct immutable types. Core imports neither Harbor nor API/web packages.
- Evidence: `docs/implementation/evidence/ENG-002/verification.md`, `tests/test_core_contracts.py`, and generated `schemas/`.

## ENG003-001 — Store bytes by digest, authorize through references

- Date: 2026-09-14
- Status: accepted for local artifact storage
- Decision: place local content-addressed bytes in `aieb-runner` behind a narrow store interface. Keep candidate file operations in the existing `aieb-core` CandidateManifest; store access is granted only by explicit scoped/public references, not by knowledge of a deduplicated digest.
- Consequence: local collection/replay preserves allowed untracked additions, modifications, and deletions without relying on Git. Invalid workspace entries reject the submission rather than being omitted. Hosted object storage remains deferred behind the same interface.
- Evidence: `docs/implementation/evidence/ENG-003/artifact-store.md` and `tests/test_candidate_artifacts.py`.

## ENG004-005-001 - Keep RAG-01 evaluation out-of-process and HTTP-only

- Date: 2026-09-14
- Status: accepted for the development task
- Decision: implement the RAG-01 candidate application as a small standard-library HTTP service. Start it from a fresh copied candidate repository and have the maintainer evaluator use only the public `/health`, `/docs`, and `/search` endpoints. The evaluator records backend mutation writes through a separate local HTTP ledger and never imports candidate modules.
- Consequence: the authoritative result comes from live application behavior, while trusted fixture generation, evaluator logic, answer expectations, and repairs remain outside the contestant build context. The ledger is limited to the published incremental-write constraint; it is not a general security monitor or cost ledger.
- Evidence: `suites/dev/rag.document-freshness/`, `tests/maintainer/rag01/`, and `docs/implementation/evidence/ENG-004-005/admission-report.md`.

## ENG004-005-002 - Admit the fixture locally but retain the independent-review gate

- Date: 2026-09-14
- Status: accepted
- Decision: mark ENG-004 and ENG-005 complete after the baseline, two independent valid repairs, five targeted counterexamples, and ten fresh reference resets pass their defined local gates. Record independent human review as pending rather than treating local automated admission as an official release approval.
- Consequence: RAG-01 may serve the next local execution/replay phase, but must not be represented as an officially admitted task, benchmark result, or published score.

## ENG006-007-001 - Make stop precede artifact freeze and fresh replay mandatory

- Date: 2026-09-14
- Status: accepted for deterministic local development
- Decision: own the attempt lifecycle in `aieb-runner` rather than relying on backend lifecycle hooks. Stop the owned engineering process tree before collecting candidate bytes; reconstruct only the collected artifact over the frozen base in a separate build allocation before external evaluation.
- Consequence: verification cannot become extra editing time, and an empty candidate remains an explicit submission rather than a fallback to the engineering workspace. Content-addressed artifacts and attempt evidence persist while writable allocations are removed.
- Evidence: `packages/aieb-runner/src/aieb_runner/lifecycle.py`, `tests/test_attempt_lifecycle.py`, and `docs/implementation/evidence/ENG-006-007/vertical-lifecycle.md`.

## ENG006-007-002 - Do not present the local adapter as official isolation

- Date: 2026-09-14
- Status: accepted
- Decision: use host processes and filesystem copies only for deterministic development lifecycle coverage. Record every unsupported isolation guarantee as blocked instead of implying it through the word “sandbox”.
- Consequence: official egress, metadata, secrets, host filesystem, VM/container, kernel resource, and multi-tenant protections remain ENG-019 work; real-agent compatibility remains the separate ENG-001 authorization gate.

## ENG008-009-001 - Reconcile receipts by physical request and preserve unknown billing

- Date: 2026-09-14
- Status: accepted for local accounting
- Decision: use attempt, role, request ID, and physical retry as the receipt identity. Broker observations reconcile adapter observations for the same identity and do not add a second charge. A lost response remains billing-uncertain with unavailable cost/tokens.
- Consequence: reports cannot mistake unknown for zero or double-count broker and adapter data. Hard-cost profiles fall back to explicit estimated/time-limited handling when conservative provider reservation is unavailable.

## ENG008-009-002 - Keep the first CLI local, frozen, and narrow

- Date: 2026-09-14
- Status: accepted
- Decision: implement a standard-library `aieb` CLI only for RAG-01 baseline/reference deterministic development candidates. Persist local versioned JSON/JSONL state under `.aieb/runs`, lock one controller, require matching frozen manifests on resume, and generate static HTML without a hosted dependency.
- Consequence: the full local vertical path is executable and inspectable without claiming generic campaign support, provider billing, credentials, real-agent success, or official results.

## ENG010-001 - Use external HTTP behavior for new task-family scoring

- Date: 2026-09-14
- Status: accepted for development admission
- Decision: EXT-02 scores document correspondence from live extraction API output under shuffled batches and partial failure; TOOL-01 scores workflow responses against an evaluator-owned operation ledger. Both candidate applications run as separate processes and evaluators avoid candidate imports.
- Consequence: shortcut controls must satisfy the public contract through live behavior, not evaluator implementation details or candidate-reported logs.

## ENG011-012-001 - Freeze offline pilot preparation without fabricating authorization

- Date: 2026-09-14
- Status: accepted
- Decision: implement metric computation and freeze the 18-cell deterministic fixture matrix, but mark the real development pilot blocked because no authorized provider/model/cap configuration exists.
- Consequence: no fixture result is presented as agent quality, campaign evidence, enforceable cost data, or an official development pilot.

## ENG013-001 - Give each new task family its own genuine application, not a shared stub

- Date: 2026-09-14
- Status: accepted for development admission
- Decision: author RAG-02/03/04, EXT-01/03/04, and TOOL-02/03/04 each as an independently named synthetic HTTP application (`search_service`, `citation_service`, `embedding_service`, `missingness_service`, `unit_service`, `batch_service`, `write_service`, `session_service`, `correction_service`) rather than reusing one shared backend across tasks. Combined with the five prior families, this gives twelve distinct application projects against a six-project development diversity floor.
- Consequence: no task's admission evidence depends on another task's application code, and exceeding the floor is recorded as such rather than presented as an unmet six-project target.

## ENG013-002 - Generalize CLI task dispatch instead of adding another per-task branch

- Date: 2026-09-14
- Status: accepted
- Decision: replace the CLI's per-task if/elif evaluator dispatch with a `TASK_RUNTIMES` mapping keyed by task ID, resolved by dynamic import at `validate`/`verify`/`run` time. Tasks whose admission script returns a full matrix use a `run_matrix` lookup instead of a single-variant evaluator call.
- Consequence: `aieb` supports all twelve catalogued tasks through one code path; admitting a future task requires a mapping entry, not a new CLI branch.

## ENG013-003 - Record structured admission evidence per task family, not only narrative summaries

- Date: 2026-09-14
- Status: accepted
- Decision: alongside the narrative `local-admission.md`, persist the full per-check matrix and ten-reset observations for every newly authored task in `docs/implementation/evidence/ENG-013/admission-report.json`, matching the structured evidence pattern established for RAG-01 in ENG-004-005.
- Consequence: local admission claims for RAG-02/03/04, EXT-01/03/04, and TOOL-02/03/04 are backed by inspectable per-check evidence, not summary prose alone.

## ENG013-004 - Do not create an admission ledger or release manifest ahead of independent review

- Date: 2026-09-14
- Status: accepted
- Decision: do not add a standalone "admission ledger" artifact or a frozen admitted-only release manifest for the development suite. `suites/dev/catalog.json` already records `review_status: pending-independent-review` for all twelve tasks, and this file (`DECISIONS.md`) is the project's existing decision ledger; a second, parallel ledger would duplicate it without adding evidence.
- Consequence: ENG-021's independent review gate remains the actual precondition for a release manifest; no tooling exists yet that would let a frozen manifest be produced before that gate clears.

## ENG013-005 - Complete the missing dev_data/dev_tests/environment fixtures on seven tasks

- Date: 2026-09-14
- Status: accepted
- Decision: `rag.embedding-version`, `ext.missingness`, `ext.unit-normalization`, `ext.partial-batch`, `tool.idempotent-write`, `tool.session-isolation`, and `tool.corrected-arguments` were missing the `dev_data/`, `dev_tests/`, and `environment/` directories the specification requires for every task (spec table row: "Visible diagnostic examples: Yes") and that every other catalogued task already had. Add task-specific sample records/queries/jobs that were verified against each task's own reference implementation, a `dev_tests/README.md` stating what remains maintainer-only, and an `environment/README.md` stating runtime requirements.
- Consequence: all twelve catalogued tasks now share the same required directory shape; no task's local admission evidence rests on an incomplete package.

## ENG014-001 - Persist section 30's table list directly; leave cohort/protocol/budget as request-supplied registry input

- Date: 2026-09-14
- Status: accepted for the hosted metadata API
- Decision: `services/api` persists exactly the tables spec section 30 names (task/evaluator/fixture revision, suite release/task, entrant revision, campaign, trial, attempt, work_item, candidate, evaluation, artifact/ref, usage_request/receipt, publication, review, audit_event) plus `idempotency_record` for API-01. Section 30 does not list separate hosted tables for Cohort/ProtocolRevision/BudgetProfile, so `POST /v1/campaigns/{id}/freeze` accepts them in the request body and resolves the draft through the existing `aieb_core.planner.freeze_campaign`, the same pure function the local CLI already uses.
- Consequence: no unlisted table was invented to work around an ambiguity; if cohort/protocol/budget persistence turns out to be required, it is a scoped follow-up ticket, not a silent addition here.

## ENG014-002 - OIDC auth fails closed; the test identity provider cannot run outside tests

- Date: 2026-09-14
- Status: accepted
- Decision: `JWKSIdentityProvider` verifies RS256 tokens against a real issuer/JWKS/audience. When `AIEB_OIDC_ISSUER`/`AIEB_OIDC_JWKS_URL`/`AIEB_OIDC_AUDIENCE` are unset, no provider is configured and every authenticated route returns 401. `TestIdentityProvider` (HS256 shared secret) raises `RuntimeError` in its constructor unless `AIEB_ENV=test`.
- Consequence: an unconfigured production deployment cannot silently allow requests through; a test-only bypass cannot be wired into production by a missed environment variable alone.

## ENG014-003 - Test against a real, disposable PostgreSQL instance, not sqlite

- Date: 2026-09-14
- Status: accepted
- Decision: `tests/test_api_service.py` requires `AIEB_DATABASE_URL` pointing at a real PostgreSQL instance (a disposable Docker container in this session, isolated on port 5544 from an unrelated project's Postgres already running on 5432) and skips rather than substituting sqlite when it is unset. Running against real Postgres caught two real bugs during development: a test fixture using a non-existent foreign-key owner, and a test fixture using the wrong `profile_compatibility` value against the planner's actual compatibility rule — neither would have surfaced against a mocked or sqlite-backed session.
- Consequence: API-01/API-02/migration-compatibility evidence reflects actual PostgreSQL constraint enforcement, not an approximation.

## ENG014-004 - Defer TypeScript client generation until apps/web exists (superseded)

- Date: 2026-09-14
- Status: superseded (see AUDIT-004 below)
- Decision: `scripts/generate_openapi.py` regenerates `docs/implementation/evidence/ENG-014/openapi.json` reproducibly, satisfying "generate OpenAPI ... artifacts." Generating a TypeScript client from it is deferred until `apps/web` exists (ENG-016); a client with no consumer would be premature scaffolding, consistent with BOOT-003's decision to defer `apps/web` until its own ticket.
- Consequence: when ENG-016 begins, it generates the TS client from this same checked-in OpenAPI schema rather than duplicating API definitions by hand.
- Superseded: Prompt 11's own text is "Generate OpenAPI **and typed client artifacts**" - both are Prompt 11 deliverables, not something ENG-016 owns; "no consumer yet" was true of `openapi.json` too when it was first checked in, and wasn't treated as a reason to withhold that artifact. `scripts/generate_typescript_client.py` now generates `docs/implementation/evidence/ENG-014/api-client.d.ts` from the checked-in schema via `openapi-typescript`; ENG-016 imports these generated types rather than duplicating API definitions by hand, exactly as this decision's consequence already anticipated.

## ENG014-005 - Replace read-then-write concurrency control with atomic conditional writes

- Date: 2026-09-14
- Status: accepted
- Decision: an independent review of the ENG-014 PR found that `PATCH /v1/campaigns/{id}` and `POST /v1/campaigns/{id}/freeze` checked `state`/`revision` in Python after a plain `session.get()`, then wrote unconditionally - a classic TOCTOU race where two concurrent requests could both pass the check and the second would silently clobber the first instead of getting 412/409. Replace both with a single atomic `UPDATE ... WHERE id=... AND state='draft' AND revision=...` statement whose `rowcount` determines success; a 0-row result triggers a diagnostic read only to classify the error (404/409/412), never to decide whether the write happens. The review also found `idempotency.check_or_reserve`'s existence check and insert were not atomic, so two concurrent requests with the same key could both pass the check and the second would raise an unhandled `IntegrityError`. Added `idempotency.finalize`, which commits the caller's business-logic writes together with the idempotency record inside one transaction and catches the `(scope, key)` unique-constraint violation: on conflict it rolls back (discarding the loser's writes) and replays the winner's stored response, or raises 409 if the bodies differ.
- Consequence: all three races are now verified under genuine concurrent execution (`tests/test_api_service.py`'s `test_concurrent_*` tests use real threads racing against the real test Postgres instance, not simulated sequential calls), and the database's own row-level locking - not applicaton-level timing - is what makes the guarantee hold.

## ENG014-006 - Trial reads are role-gated; corrupt stored manifests return 503, not 500

- Date: 2026-09-14
- Status: accepted
- Decision: the same review found `GET /v1/trials/{id}` required only a valid bearer token with no role check, unlike the neighboring artifact-download endpoint, letting any authenticated identity read raw trial internals for any campaign. Changed it to `require_role("operator", "reviewer", "administrator")`, matching spec section 3's permission table; per-trial ownership scoping (e.g. a submitter seeing only their own campaign's trials) remains deferred since campaigns have no owner column yet. The review also found `TaskRevision.model_validate`/`EntrantRevision.model_validate` on a stored manifest could raise an unhandled `pydantic.ValidationError` on read, and that `JWKSIdentityProvider`/`TestIdentityProvider.verify` could raise an unhandled `KeyError` on a token missing `sub`, both surfacing as bare 500s instead of the module's promised typed-error/fail-closed behavior. Added a `service_unavailable` (503) error for a stored record failing its own contract - the record exists, so 404 would misrepresent it, and a client retry cannot fix server-side corrupt data - and widened the auth `except` clauses to catch `KeyError` alongside `jwt.PyJWTError`.
- Consequence: no route in this ticket can turn a data-integrity fault or a malformed token into an unhandled exception; every failure mode maps to one of the typed errors spec section 33 defines.

## ENG014-007 - A second review pass found the first fix pass incomplete, not wrong

- Date: 2026-09-14
- Status: accepted
- Decision: a second independent review of ENG014-005/006 found the corrupt-manifest guard was ported to `registry.py` but not to the equivalent `TaskRevision`/`EntrantRevision.model_validate` calls inside `freeze()` (same bug, sibling call site); that two concurrent freeze calls sharing the same Idempotency-Key both pass `check_or_reserve` before either commits, so the atomic-UPDATE loser fell into the `conflict()` branch and returned 409 to what was actually a legitimate retry, instead of `finalize()`'s replay path; and that freeze's atomic guard checked `state='draft'` but not `revision`, so a PATCH committing between freeze's initial read and its final write would be silently discarded rather than detected. Fixed by: extracting `revisions.validate_stored_manifest` and using it at both call sites; re-running `check_or_reserve` in the freeze UPDATE's zero-rowcount branch before concluding a real conflict, since the loser's failure may just be the winner's own commit becoming visible under READ COMMITTED; and capturing the draft's `revision` at freeze's initial read and including it in the final UPDATE's WHERE clause, mirroring `patch_campaign`. Also fixed two lower-severity items the same review raised: `session.get()` after a raw Core UPDATE relied on SQLAlchemy's `synchronize_session='evaluate'` to keep the identity-mapped object fresh, which is version-dependent - both `patch_campaign` and `freeze` now use `.returning(CampaignRow)` to get the authoritative post-write row directly from Postgres; and `idempotency.finalize`'s `except IntegrityError` assumed the cause was always the idempotency unique constraint, which would crash with an unhandled `NoResultFound` on any other constraint violation in the same transaction - it now checks `exc.orig.diag.constraint_name` and re-raises anything that isn't `uq_idempotency_scope_key`.
- Consequence: the same-key-retry race, the revision-blind freeze guard, and the sibling corrupt-manifest call site are each covered by a dedicated test using real thread concurrency or direct fault injection (`test_concurrent_freeze_same_idempotency_key_replays_not_409`, `test_freeze_detects_concurrent_patch_and_does_not_silently_drop_it`, `test_freeze_with_corrupt_referenced_manifest_is_503_not_500`, `test_finalize_reraises_unrelated_integrity_error`), not just asserted fixed by inspection.

## ENG015-001 - One `engineering` work item per attempt; verification is not independently leased

- Date: 2026-09-14
- Status: accepted for this ticket's scope
- Decision: the architecture diagram (section 9) shows a separate execution worker and verification worker, but `aieb_runner.lifecycle.LocalAttemptRunner.run()` already performs engineer→stop→collect→build→verify as one reused, unmodified call. Splitting that into two independently leasable `work_item` phases would mean either duplicating the existing pipeline's internal ordering in the leasing layer or invasively restructuring `LocalAttemptRunner` - both of which the prompt's "reuse the proven local execution pipeline... avoid separate hosted scoring logic" instruction argues against. This ticket therefore leases one `work_item` of type `engineering` per attempt, covering the whole call.
- Consequence: a verification-only outage cannot be distinguished from an engineering-phase outage at the leasing layer today (both surface as the same work item's outcome); splitting the phases is real future work, not silently claimed as done here.

## ENG015-002 - Extend LocalAttemptRunner with an optional, backward-compatible cancel_event

- Date: 2026-09-14
- Status: accepted
- Decision: hosted campaign cancellation needs to interrupt an attempt that is still engineering, not only stop dispatching new ones. `LocalAttemptRunner.run()`'s single blocking `process.communicate(timeout=deadline)` call could not be interrupted early. Replaced it with a bounded poll loop (`process.poll()` / `time.monotonic()` / `time.sleep(poll_interval)`) that also checks an optional `threading.Event` passed in as `cancel_event`; when set, the attempt stops immediately with `ExecutionValidity.CANCELLED` and no verdict, mirroring the deadline path's existing stop/collect discipline. The parameter defaults to `None` and every existing call site is unaffected.
- Consequence: `tests/test_attempt_lifecycle.py::test_cancel_event_stops_engineering_before_deadline_with_no_verdict` and all six pre-existing lifecycle tests pass unchanged, confirming no regression from this extension.

## ENG015-003 - Worker/reconciler code lives in services/api, depending on aieb-runner

- Date: 2026-09-14
- Status: accepted
- Decision: the monorepo table (spec section 11) lists `aieb-runner`'s allowed dependencies as "core, Harbor adapter dependencies" and `services/api`'s as "core, analysis; workers communicate through repository/service layer" - neither explicitly names where PostgreSQL-backed worker leasing code should live, and the table predates any of this code existing. Given the leasing/fencing logic needs the SQLAlchemy models ENG-014 already built in `aieb_api.models`, and the actual engineering execution needs `aieb_runner.lifecycle.LocalAttemptRunner`, this ticket adds `aieb-runner` as a dependency of `aieb-api` and places `aieb_api/worker/` (repository, runner_bridge, loop, reconciler, metrics) there rather than inventing a third package or duplicating the persistence models into `aieb-runner`.
- Consequence: "workers communicate through repository/service layer" is honored as `aieb_api/worker/repository.py` - every DB state transition worker/reconciler code needs goes through that one module, not ad hoc queries scattered across `loop.py`/`reconciler.py`.

## ENG015-004 - Replacement creates a new attempt/work_item; it does not reclaim the same row

- Date: 2026-09-14
- Status: accepted
- Decision: spec section 16 says a replacement "keep[s] original trial ID, increment[s] attempt number, and reset[s] engineering state completely." `reconcile_expired_leases` therefore marks an expired, candidate-less work item permanently `failed` and creates a *new* `AttemptRow` (`number + 1`) with its own new `work_item`, rather than resetting the same row's state back to `ready` with a bumped generation. A stale worker returning after reassignment is rejected because its old work item's `state` is no longer `'leased'` (not because of a generation mismatch on a row it could otherwise still reach).
- Consequence: `tests/test_worker_leasing.py::test_stale_worker_finalize_after_lease_reassignment_is_rejected` asserts the fence via the replacement's *different* work_item/attempt identity, not via a same-row generation bump - this is what the implementation actually does, and an earlier draft of that test assumed the wrong model until a real Postgres run caught it.

## ENG015-005 - Test "death during engineering" with a real killed OS subprocess, not simulated state

- Date: 2026-09-14
- Status: accepted
- Decision: five of the eight controlled-failure scenarios (contention, death-before-launch, death-after-upload, stale-worker, duplicate-completion) are tested by directly driving the repository functions to the exact state of interest, since that is the state layer actually under test. "Death during engineering," however, is specifically about what a real killed process leaves behind (an orphaned `engineer/` allocation `LocalAttemptRunner`'s own cleanup never ran for) - a claim that direct state manipulation cannot honestly verify. That one test spawns a real `subprocess.Popen` child that claims a work item and runs a deliberately slow (20-second) engineering script, kills it with `Process.kill()` mid-sleep, and asserts the orphaned directory both exists before and is actually removed by `teardown_orphan_allocations` after.
- Consequence: running this test genuinely exercises the Windows job-object process-tree teardown wired up in ENG-006/007's `LocalAttemptRunner._stop_tree`/kill-on-job-close mechanism, not a mock of it.

## ENG015-006 - Fold record_outcome's fencing check into a real UPDATE; drive orphan cleanup from the reconciler's own locked decision

- Date: 2026-09-14
- Status: accepted
- Decision: a review of the ENG-015 PR found two genuine TOCTOU races. First, `record_outcome`'s fencing check was a plain `SELECT`, which takes no row lock under READ COMMITTED - a concurrent reconciler sweep could expire the same lease and create a replacement attempt after that check passed but before `record_outcome`'s own commit landed, letting an already-abandoned worker's results land anyway. Fixed by making the fencing check a real `UPDATE ... WHERE ... RETURNING` (which also extends the lease, doubling as an implicit heartbeat), so it takes the same row lock the reconciler's `SELECT ... FOR UPDATE SKIP LOCKED` contends for - whichever transaction locks the row first durably wins, and the loser's own WHERE clause fails once it can proceed. Second, `teardown_orphan_allocations` decided which attempts were "orphaned" via its own separate, unlocked, point-in-time `SELECT` (`teardown_orphans`), disconnected from `reconcile_expired_leases`'s actual locked decision - a live worker whose heartbeat was merely delayed (GC pause, slow round-trip) could look expired to that independent read and have its working files deleted, even though the worker's heartbeat succeeds moments later and the reconciler's own locked pass never touches that row. Fixed by having `reconcile_expired_leases` return `orphaned_attempt_ids` - exactly the attempts it just committed as replaced, from inside the same `FOR UPDATE SKIP LOCKED` pass - and having the reconciler delete files only for those IDs, removing the separate `teardown_orphans` query entirely.
- Consequence: `test_concurrent_reconciler_cannot_race_a_record_outcome_still_in_flight` calls the real `record_outcome` (not a reimplementation), using a `before_commit` session event to hold its row lock open while a genuine concurrent reconciler sweep runs in a second thread; it was verified to fail against the pre-fix code (temporarily reverted, confirmed to fail, restored) before being kept as a permanent regression test. `test_reconciler_never_orphans_a_lease_a_live_worker_just_re_extended` confirms a lease a real heartbeat call just extended is never in `orphaned_attempt_ids` and its files are never touched.

## AUDIT-001 - An independent whole-project review found real defects across ENG-002/009/011/013/014/015; critical/security items fixed first

- Date: 2026-09-15
- Status: accepted
- Decision: an independent review spanning Prompts 01-12 raised 17 findings. Four were independently verified as critical and fixed immediately, with the rest deliberately left for a follow-up triage pass rather than fixed reflexively:
  1. `aieb task validate` (`packages/aieb-cli/src/aieb_cli/main.py::_task_check`) never called `TaskRevision.model_validate()` - it checked only that required files existed and that the image string contained `@sha256:`. This let five of twelve task.yaml files pass CLI validation despite failing the actual contract: `rag.document-freshness`'s five digest fields were unquoted YAML, parsed as integers rather than the required strings; `tool.corrected-arguments`/`tool.idempotent-write`/`tool.session-isolation` declared `category: tool` (not the valid `tool_app`); `tool.false-completion`/`tool.idempotent-write` declared invalid `egress_policy` values (`operation-service-only`, `fixture-only`, neither in the allowed `none`/`allowlist` set). `_task_check` now validates the real `TaskRevision` model; all five task.yaml files were corrected (quoted digests, corrected enum values) and now pass genuine validation, confirmed by running `aieb task validate` against all twelve tasks.
  2. `aieb_api/auth.py` copied `aieb_roles` directly out of bearer-token claims as `Identity.roles`, and `require_role` checked against that - the persisted `role_bindings` table (built in ENG-014) was never queried. An identity provider capable of issuing arbitrary role claims therefore controlled authorization directly, violating spec section 3's "role assignments are stored server-side." Fixed: `Identity` now carries only `subject`/`issuer` (who the token proves you are); `resolve_roles(session, identity)` is the sole source of authorization, querying `users`/`role_bindings` by `(oidc_issuer, oidc_subject)`; an identity with no matching `users` row resolves to zero roles regardless of what its token claims. The same review surfaced a related bug in `authorized.py::download_artifact`: the private-artifact ownership check compared `identity.subject` (a raw OIDC claim string) against `ref.owner_user_id` (an internal `users.id` UUID) - different value spaces that could never actually match, so ownership-based access silently never worked even before this fix. Both are now resolved through the same `users` lookup.
  3. `aieb_api/worker/loop.py` checked campaign cancellation exactly once, immediately after claiming a work item, and passed a pre-set-or-not `threading.Event` into `execute_leased_work` with no further updates. A cancel issued after an attempt was already engineering had no effect until that attempt finished naturally or hit its own deadline - the existing test only covered "already cancelling before claim," never a cancel arriving mid-run. Fixed: `execute_leased_work` now runs a second background thread (`_cancellation_poll_loop`, alongside the existing heartbeat thread) that polls `is_campaign_cancelling` for the duration of the attempt and sets the shared `cancel_event` as soon as cancellation is detected, interrupting `LocalAttemptRunner.run()`'s poll loop (added in ENG015-002) immediately rather than waiting for it to finish.
  4. `aieb_analysis.metrics.summarize()`'s completeness check (`incomplete = ... any(len(values) != required_repetitions for values in cells.values())`) only ever inspected `(task, entrant)` keys that already had at least one observation; a task/entrant cell with zero observations has no key at all and was invisible to the check, so a wholly missing planned trial could report `complete_for_rank: True`. Fixed: added an optional `planned_cells` parameter - when the caller supplies the frozen campaign's actual expected cell set, any cell absent from the observations is now checked explicitly, not inferred from what happens to be present. Left backward-compatible (`planned_cells=None` preserves prior behavior) since no caller yet supplies the full plan; wiring a real caller through is part of the follow-up triage, not invented here.
- Consequence: all four fixes are covered by tests exercising the real code paths (`test_wholly_missing_planned_cell_blocks_rank`, `test_private_artifact_ref_denied_without_leakage` now exercising a working ownership path, `test_cancellation_arriving_mid_run_interrupts_the_attempt`, and the full `aieb task validate` run against all twelve tasks). The remaining 13 findings (placeholder digests across the suite, publication-endpoint status/auth gating, verification not independently leased as its own work-item type, TOOL-02's ambiguity fixture fidelity, the "twelve projects" diversity claim, persistence-level immutability triggers, snapshot-digest integrity verification, a Windows drive-path extraction edge case, blanket `RuntimeError` candidate attribution, and the worker's dependency on `tests.maintainer.*`/`suites/dev` as its evaluator registry) are deliberately not fixed in this pass - they are tracked for a follow-up triage that verifies each against the code before deciding remediation, the same discipline used for the four fixed here.

## AUDIT-002 - Second triage batch: three more AUDIT-001 findings fixed (a real Windows path escape, blanket RuntimeError attribution, and unenforced persistence immutability)

- Date: 2026-09-15
- Status: accepted
- Decision: continuing the AUDIT-001 triage, three more findings were verified as real and fixed:
  1. (Finding #15) `aieb_runner.artifacts._safe_relative` and the identical logic duplicated in `aieb_core.models.CandidateFile`/`SubmissionPolicy` rejected paths starting with `/` or containing `..`, but not a Windows drive-qualified path like `C:/outside`. Verified directly: `PurePosixPath("C:/outside").is_absolute()` is `False` (POSIX has no drive-letter concept), yet `Path(destination) / "C:/outside/evil.txt"` resolves to `C:\outside\evil.txt` on Windows - the drive letter becomes a new anchor and silently discards `destination`. This is reachable today through `reconstruct_candidate`, which every real attempt goes through. Fixed by rejecting any leading `[A-Za-z]:` segment at all three call sites (a shared `_has_escaping_segment` helper in `aieb_core.models`, plus `aieb_runner.artifacts`'s own copy - the packages cannot share code across the dependency boundary). Also hardened `safe_extract_tar` (not yet wired into any production path, but exported for future use) to resolve and verify every extraction target stays under the destination root, since a *string-safe* member name can still resolve outside an already-existing (reused, not freshly created) destination that contains a symlinked intermediate directory - real symlink creation is blocked by a Windows privilege in this sandboxed environment (confirmed: WinError 1314), so the test simulates the resolved-outside-root condition by patching `Path.resolve` for the one path of interest, the same technique the pre-existing `test_rejects_symlink_escape` already uses for the same reason.
  2. (Finding #16) `LocalAttemptRunner.run()` treated any `RuntimeError` from the evaluator as `CANDIDATE_RUNTIME_FAILURE` (scored FAIL) and anything else as `SCORER_ERROR`. `RuntimeError` is a generic, widely-raised Python type with no special meaning reserved for "the candidate is broken" - a trusted evaluator's own unrelated bug raising a bare `RuntimeError` would be misattributed as a scored candidate failure. Confirmed the project's actual evaluators (`tests/maintainer/common.py::CandidateProcess`, used by 11 of 12 task evaluators, and `rag01/evaluator.py`'s own `CandidateService`) only ever raised `RuntimeError` for the one genuinely candidate-side condition ("candidate process/service stopped or never became ready"), so this was a design fragility rather than an actively misfiring bug. Fixed by introducing `aieb_runner.lifecycle.CandidateUnavailableError`, raised explicitly by both harnesses instead of bare `RuntimeError`, and caught explicitly in place of it - a bare `RuntimeError` from any other source now correctly falls through to `SCORER_ERROR`. Verified the new test fails against the pre-fix code (temporarily reverted, confirmed failure, restored) before keeping it.
  3. (Finding #13) Prompt 11 explicitly required immutable frozen revisions enforced "in persistence, not only in UI checks," but nothing below the API route layer stopped a direct `UPDATE` against `task_revision`/`evaluator_revision`/`entrant_revision`/`fixture_revision`, or against a frozen campaign's `draft`/`resolved`/`manifest_digest`/`cohort_digest`. Confirmed no application code anywhere ever legitimately updates the four revision tables. Added an Alembic migration creating `BEFORE UPDATE` triggers: a shared `aieb_reject_revision_update()` function on all four revision tables (any update raises, surfacing through SQLAlchemy as `IntegrityError`), and `aieb_reject_frozen_campaign_mutation()` on `campaign`, which blocks changes to the frozen manifest fields once `state <> 'draft'` while still permitting legitimate lifecycle writes (state transitions, `revision` bumps) - verified both the block and the still-permitted case directly against real Postgres, plus a manual `psql` UPDATE to confirm the trigger fires outside the ORM entirely, not just when called through application code.
- Consequence: all three are covered by tests against the real code/database (`test_rejects_windows_drive_qualified_paths`, `test_safe_tar_rejects_windows_drive_qualified_member_name`, `test_safe_tar_rejects_escape_through_a_symlinked_existing_destination`, `test_bare_runtime_error_from_evaluator_is_scorer_error_not_candidate_failure`, `test_task_revision_row_rejects_direct_update_at_the_database_level`, `test_frozen_campaign_manifest_rejects_direct_update_but_state_can_still_change`), and the full regression suite (83 tests across core/CLI/API/worker/analysis/artifacts) still passes. Ten findings remain from the original AUDIT-001 list: placeholder digests across every task.yaml, publication endpoint status/auth gating (re-verified during this pass: not currently exploitable, since only `published`/`withdrawn`/`superseded` exist and all three are meant to stay public per spec sections 26/33/36 - a real gate is only needed once ENG-018 introduces a non-public status), whether "twelve distinct family IDs" reflects genuine project diversity, TOOL-02's ambiguity-simulation fidelity, the catalog's "validated" label given remaining open items, ENG-012's pilot preparation not being a fully resolved campaign, ENG-011's per-entrant/category/cost-accounting completeness, snapshot-digest integrity verification on publication reads, and the worker's dependency on `tests.maintainer.*`/`suites/dev` as a development-only evaluator registry.

## AUDIT-003 - Final triage batch: the remaining ten AUDIT-001 findings resolved (four with code fixes, six by verification/documentation)

- Date: 2026-09-15
- Status: accepted
- Decision: completed the triage of the original 17-finding review. Four more findings were verified as real and fixed:
  1. (Finding #2) Every `suites/dev/*/task.yaml`'s `repository_digest`/`provenance_digest`/`contract_digest`/`service_topology_digest`/`evaluator_digest` was a repeated-digit placeholder (`"1111...1"`, `"2222...2"`, ...) that never reflected any actual content - they could not detect drift and were not verifiable claims, only plausible-looking hex strings. Four of the five name content this repo genuinely has (the task's `repo/` tree, `provenance.json`, `contracts/application-api.md`, `environment/README.md`, and the trusted evaluator's own source file); `scripts/compute_task_digests.py` computes real SHA-256 digests from that on-disk content and rewrote all twelve task.yaml files. `environment.official_image`'s digest is deliberately left alone: it names a container image this project has never built or pushed (the registry host itself, `registry.example`, is fictional), so there is no real content for a digest to verify against, and computing one would not be more honest than the placeholder already is. To keep this from silently going stale again, `aieb_cli.main._task_check` now recomputes all four content digests (and `evaluator_digest` when the task has a registered runtime) and rejects a task.yaml whose claimed value does not match, rather than only checking the value's shape.
  2. (Finding #9) TOOL-02's fixture server always returned `{"status": "completed"}` immediately on every request, so its "ambiguous-retry-resolves" check never exercised genuine ambiguity - any backend that received a normal response passed, regardless of whether it handled a lost acknowledgment correctly. Confirmed by reading the fixture and all four backend variants: the "broken" baseline and the "correct" reference/alternative both unconditionally sent two requests regardless of any actual failure signal. Fixed: the fixture now commits the write (into the effect ledger the checks inspect) and drops the connection without responding on the *first* request for each identity, exactly like a real lost acknowledgment; a retry with the same identity gets the normal completed response. Reference/alternative were corrected to catch the resulting connection error and retry with the same `Idempotency-Key`; the baseline was corrected to retry without a key (so each retry is a new, unresolvable identity to the server - it now fails both checks instead of trivially passing); the `never-retry` counterexample now makes one real keyed request and gives up on failure instead of never attempting one. Verified via `scripts/run_tool02_admission.py`: baseline fails both checks, reference/alternative pass fully, `never-retry` fails only `ambiguous-retry-resolves`, and all ten reference resets still pass.
  3. (Finding #8) `aieb_analysis.metrics.summarize()` emitted exactly one combined suite-wide rate across every entrant and category together, with no way to recover a rate for one entrant or one task category, and folded engineer+dev-application cost together while silently omitting verifier cost entirely - spec section 30 requires the results table to show per-category rates and requires verifier/infrastructure expense to be reported separately from cost-per-resolution, not folded in or dropped. Fixed: added an optional `category` field to `TrialObservation`; `summarize()` now derives `per_entrant` (a fixed-weight rate per entrant across that entrant's own task cells) and `per_category` (same, grouped by task category, `None` when no observation carries one) from the same per-task cells already computed, and reports a standalone `verifier_cost_total_usd` (unavailable, not silently zero or omitted, if any observation is missing it) alongside the existing `cost_per_resolution`. Backward-compatible: `category` defaults to `None` and no existing caller or test needed to change.
  4. (Finding #14) Nothing recomputed a publication's `snapshot_digest` against its stored `snapshot` JSONB before serving it as canonical public results via `GET /v1/publications/{id}/results` or `GET /v1/comparisons` - a corrupted or hand-edited row would be served with no integrity check catching the mismatch, the same class of gap AUDIT-002's revision-immutability triggers closed for task/evaluator/entrant/fixture revisions and frozen campaigns. Fixed with the same two-layer approach: a new migration adds a `BEFORE UPDATE` trigger rejecting any change to `publication.snapshot`/`snapshot_digest` (status may still transition published/withdrawn/superseded), and both read routes now recompute `content_hash(row.snapshot)` and compare it to `row.snapshot_digest` before serving, raising `503 service_unavailable` (a data-integrity fault, not a client error or a hidden 404) on a mismatch instead of silently serving corrupted results.
- The remaining six findings were verified against the current code/docs and resolved without a code change, each because the concern the review raised turned out to already be true or already honestly stated, not because it was waved off:
  5. (Finding #4, re-confirmed from AUDIT-002) Publication endpoint status/auth gating remains not currently exploitable - unchanged conclusion.
  6. (Finding #10) Whether "twelve distinct family IDs" is genuine diversity or renamed near-duplicates: an independent read of all twelve `repo/*/backend.py` files found each encodes a genuinely different bug/domain (stale RAG chunks, wrong filter-then-topk order, stale citation spans, ignored embedding version, fabricated missing values, output/input misalignment, wrong unit conversion, all-or-nothing batch failure, ignored completion status, missing idempotency key, global mutable session state, stale corrected arguments) over meaningfully different data models - not renamed copies of one app. The concern has real substance in a different sense, though: 9 of the 12 share a near-identical generic `BaseHTTPRequestHandler` harness (expected - that's the harness, not the tested logic) wrapping a very thin payload (2-4 lines of actual logic for several of them). Documented in STATUS.md as-is: genuinely distinct bugs tested, but shallow implementations, not overclaimed as substantial applications.
  7. (Finding #11) The catalog's "validated" label: `suites/dev/catalog.json` already carries a top-level `"review_status": "pending-independent-review"` alongside each task's `"local_status": "validated"`, and STATUS.md already says "these are local validation results, not official admission." Now that finding #1's fix makes `aieb task validate` a real check against the `TaskRevision` contract (not the hand-rolled subset it was before), "validated" is an accurate claim about what actually ran, not an overclaim - no wording change needed.
  8. (Finding #12) ENG-012's pilot preparation: `development-pilot-18.json` already carries `"state": "prepared-not-authorized"` and an explicit `"execution_blocker"` field, and its STATUS.md row is already `BLOCKED` with the same explanation. It was never presented as a resolved campaign anywhere in the repo - already correctly labeled.
  9. (Finding #17) The worker's dependency on `tests.maintainer.*`/`suites/dev` as its evaluator registry (`aieb_api/worker/runner_bridge.py`'s hardcoded task-id-to-module dict): real and worth naming, but not a defect to fix now - a production evaluator registry/distribution mechanism (how trusted evaluator code is packaged and deployed independent of this monorepo) is a design question that belongs with ENG-019's official isolation work, not something to invent speculatively ahead of that design. Documented as an acknowledged interim bridge in STATUS.md's ENG-015 row.
- Consequence: all four code fixes are covered by tests against real behavior (`tests.test_analysis`'s three new cases, `tests.test_accounting_and_cli::test_stale_content_digest_fails_validation`, `tests.test_api_service`'s four new publication-snapshot cases including a real trigger-vs-ORM check, and `scripts/run_tool02_admission.py`'s full matrix), and the full regression suite (94 tests across core/CLI/API/worker/analysis/artifacts/admission) passes. All 17 findings from the original independent review are now triaged: 11 fixed with a code change (4 in AUDIT-001, 3 in AUDIT-002, 4 here), and 6 resolved by verification or documentation with no code change needed (finding #5 was already an accepted ENG015-001 design decision predating this review; finding #4 was closed by re-verification in AUDIT-002; findings #10, #11, #12, #17 were closed by re-verification/documentation here).

## AUDIT-004 - A second, independent review of the AUDIT-001/002/003 work found five more real defects (fixed) and one genuine architecture disagreement (flagged, not unilaterally resolved)

- Date: 2026-09-15
- Status: accepted
- Decision: a second independent review examined the AUDIT-001/002/003 fixes themselves and the prompt-by-prompt state, raising six findings. Five were verified as real and fixed:
  1. `resolve_roles` (added in AUDIT-001) loaded every role a user held with no regard for `RoleBinding.scope`, even though the schema (spec section 30: "unique subject; scoped role") exists specifically to scope a grant. A role bound to one campaign/suite/test scope therefore silently satisfied any `require_role` check, including checks that should require a site-wide grant - the AUDIT-001 fix closed the "token claims grant roles directly" hole but left this second, narrower one open. Fixed: `auth.GLOBAL_SCOPE` names the site-wide scope; `resolve_roles`/`require_role` take a `scope` parameter (defaulting to `GLOBAL_SCOPE`, since every route implemented so far - `POST /campaigns`, `GET /trials`, artifact downloads - checks for a site-wide grant, none being scoped to one resource yet) and only count bindings at that scope or the global one.
  2. `summarize()`'s new `per_category` output (added in AUDIT-003 for finding #8) averaged every entrant's rate for a category into one blended number - a strong entrant's category performance could leak into a weak entrant's reported rate and vice versa, which is not what a results table showing per-entrant rows needs. Fixed: `per_category` is now `{category: {entrant: rate}}`, derived the same way `per_entrant` already was, just filtered to that category's tasks per entrant rather than collapsed across entrants.
  3. `evaluator_digest`'s verification (added in AUDIT-003 for finding #2) hashed only the single `evaluator.py` file, so evaluator behavior could still change silently through code it imports without the digest changing: 11 of 12 evaluators import `tests/maintainer/common.py` (`CandidateProcess`, `request`), and `rag01` also has its own sibling `fixture.py` the review named specifically. Fixed: `hash_evaluator_closure` hashes every `.py` file in the evaluator's own package directory plus `tests/maintainer/common.py` when present; all twelve task.yaml files were regenerated with the corrected digest, and a new test tampers with `common.py` and with `rag01/fixture.py` (restoring each afterward) to prove the digest actually moves.
  4. The checked-in `docs/implementation/evidence/ENG-013/admission-report.json` still showed TOOL-02's baseline resolving ambiguity and its `never-retry` counterexample duplicating the effect - the pre-fix behavior from before AUDIT-003's finding #9 fix, never regenerated afterward. Fixed by adding `scripts/generate_eng013_admission_report.py` (so this evidence is reproducible from a live run rather than hand-assembled) and running it; the file now matches the corrected fixture's actual output.
  5. Prompt 11's own text is "Generate OpenAPI **and typed client artifacts**" - both were named as Prompt 11 deliverables, but ENG014-004 deferred the typed client until `apps/web` exists, treating "no consumer yet" as a reason to withhold the artifact itself, which conflates the artifact with its future consumer (the same reasoning was never applied to `openapi.json`, which was checked in with no consumer either). Fixed: `scripts/generate_typescript_client.py` generates `docs/implementation/evidence/ENG-014/api-client.d.ts` from the checked-in OpenAPI schema via `openapi-typescript`; ENG014-004 is marked superseded rather than deleted, since its reasoning about *deferring apps/web itself* (BOOT-003) remains correct - only the "therefore don't generate the client artifact" conclusion was wrong.
- The sixth finding is a genuine architecture disagreement, not a verify-and-fix bug, and is not unilaterally resolved here: the review argues ENG-015 does not satisfy Prompt 12 because verification is not independently leased, heartbeated, and fenced as its own PostgreSQL work item (one `engineering` work item currently covers execute-then-verify as a single leased unit, via `LocalAttemptRunner.run()`). This is exactly the tension ENG015-001 already recorded on 2026-09-14: Prompt 12's own text is "PostgreSQL-leased execution **and verification** work" *and*, in the same paragraph, "reuse the proven local execution pipeline... avoid separate hosted scoring logic" - satisfying the second instruction as written means the two phases are not independently leasable without duplicating `LocalAttemptRunner`'s internal ordering into the leasing layer or invasively restructuring it. The review's position is that the first instruction should win; ENG015-001 judged the second should, for this ticket's scope, and disclosed the resulting gap (a verification-only outage cannot be distinguished from an engineering-phase outage at the leasing layer) rather than hiding it. Splitting the phases now is a substantial, invasive change to an already fully-tested leasing/reconciliation system (8 controlled-failure scenarios), not a contained bug fix - re-litigating it is a call for whoever is directing this work, not something to decide unilaterally mid-triage.
- Consequence: all five code fixes are covered by tests against real behavior (`tests.test_api_service::test_scoped_role_binding_does_not_grant_a_global_check`, `tests.test_analysis::test_per_category_rate_is_reported_per_entrant_not_blended_across_entrants`, `tests.test_accounting_and_cli::test_evaluator_digest_covers_the_shared_harness_and_sibling_modules`, the regenerated admission-report.json, and the new TypeScript client artifact), and the full regression suite (97 tests) passes. The ENG-015 leasing-granularity disagreement remains open, tracked against ENG015-001, pending a decision on whether to invest in splitting the leasing phases now or continue accepting the disclosed gap.

## AUDIT-005 - A third independent review confirmed one more real correctness bug, upheld the ENG-015 disagreement, and flagged unpinned generator reproducibility

- Date: 2026-09-15
- Status: accepted
- Decision: a third review reproduced a concrete failure case rather than only reading code, and found one new real defect, fixed:
  1. `summarize()`'s `complete_for_rank` counted raw observations per cell (`len(values)`) against `required_repetitions`, not valid/resolved ones. Reproduced directly: a cell with three raw observations, one of which is `execution_valid=False` (an infrastructure-invalid attempt, not a scored outcome), reported `complete_for_rank: True` and `suite_rate: 1.0` with only 2 valid trials backing it - the opposite of what "the specified number of valid final trials" should mean, and a materially different defect from AUDIT-001's finding #7 (a wholly *missing* cell, which `planned_cells` already handles) - this is a cell that has attempts, just not enough *valid* ones. Fixed by deriving the completeness check from the same per-task valid-observation count (`n`) `per_task` already computes for each cell, rather than the raw `len(values)`; `per_task` is now computed before the completeness check instead of after, since the fix depends on it. `tests/test_analysis.py::test_a_cell_with_enough_raw_attempts_but_not_enough_valid_ones_blocks_rank` reproduces the exact scenario and pins the fix. `planned_cells` still has no production caller (a pre-existing, already-disclosed gap, distinct from this bug) - AUDIT-005 downgrades ENG-011 from COMPLETE to IN_PROGRESS for that reason, since two real correctness defects in two consecutive reviews plus an acknowledged unwired integration gap is no longer just a footnote.
  2. The same review upheld AUDIT-004's flagged ENG-015 leasing-granularity disagreement and argued that documenting it as an open disagreement does not itself satisfy Prompt 12's acceptance gate - status bookkeeping, not new technical content. Agreed on the bookkeeping point specifically: ENG-015 is downgraded from COMPLETE to IN_PROGRESS in STATUS.md. The underlying architecture question (whether to invest in splitting the leasing phases) remains exactly as open as AUDIT-004 left it; this is not a technical resolution, just an honest status label while it stays unresolved.
  3. The review also flagged that `scripts/generate_typescript_client.py` ran a bare `npx --yes openapi-typescript` with no pinned generator version, so a future run could silently fetch a different release and produce different output for the same schema - a reproducibility gap of exactly the kind spec section 10 already warns against ("Do not specify 'latest' dependencies"). Fixed: pinned to `openapi-typescript@7.13.0` via a `GENERATOR_SPEC` constant; both `generate_typescript_client.py` and `generate_openapi.py` gained a `--check` mode that regenerates into a temp location and fails if it differs from the checked-in artifact, and a new CI workflow (`.github/workflows/api-artifacts.yml`) runs both checks on changes to `services/api/**` or the generator scripts themselves, so staleness or an unpinned-version drift is caught automatically rather than relying on someone remembering to regenerate.
- Consequence: the completeness fix is covered by a test that reproduces the exact scenario the review described and would fail against the pre-fix code (verified: reverting the fix reproduces `complete_for_rank: True` with `n=2` against `required_repetitions=3`, exactly as reported). The full regression suite (98 tests) passes. Both `--check` commands were run locally and confirmed to pass against the regenerated artifacts. ENG-011 and ENG-015 are now IN_PROGRESS in STATUS.md, reflecting genuinely open work (an unwired integration gap and an unresolved architecture question respectively) rather than a caveated COMPLETE.

## AUDIT-006 - A fourth independent review found one more real cost-accounting bug and one CI trigger-coverage gap

- Date: 2026-09-15
- Status: accepted
- Decision: a fourth review, again reproducing rather than only reading, found one more real defect and one real gap:
  1. `cost_per_resolution`'s numerator summed every observation's engineer+dev-application cost regardless of `execution_valid`, while its denominator was scored successes only - spec section 20/30's definition is "engineer + development-application cost for all SCORED trials / successful resolutions," and separately requires publishing total campaign cost including invalid attempts, not blending the two into one number. Reproduced directly: a $3 valid success alongside a single $200 infrastructure-invalid attempt reported `cost_per_resolution: 203.0` instead of `3.0`. Fixed by scoping the numerator (and its missing-accounting check) to `execution_valid` observations only, matching the denominator's population; added a new, deliberately broader `total_campaign_cost_usd` field (every attempt, valid or invalid) to satisfy the spec's separate "publish total campaign cost including invalid attempts" requirement, which previously had no output field at all. Verified the new test fails against the pre-fix code (reproduces `203.0`), then restored the fix.
  2. `.github/workflows/api-artifacts.yml`'s `pull_request` path filter did not include the two generated artifacts themselves (`docs/implementation/evidence/ENG-014/openapi.json`, `.../api-client.d.ts`) - a PR that hand-edited one of the checked-in artifacts directly (bypassing the generator) would not trigger the staleness check that exists specifically to catch that. Added both paths to the trigger.
  - The review's two other findings (ENG-015 verification leasing remains unimplemented; ENG-011 still has no production caller for `planned_cells`/category) are not new - both are the same already-open, already-downgraded items from AUDIT-004/005, correctly re-confirmed rather than newly discovered.
- Consequence: `tests/test_analysis.py::test_cost_per_resolution_excludes_infrastructure_invalid_attempts` and `::test_cost_per_resolution_unaffected_by_missing_cost_on_an_excluded_invalid_attempt` cover the fix and its missing-accounting boundary; the full regression suite (100 tests) passes.

## AUDIT-007 - A fifth independent review found the cost fix itself still had one gap, plus a naming/completeness problem with the new total field

- Date: 2026-09-15
- Status: accepted
- Decision: a fifth review, again reproducing directly, found AUDIT-006's own fix incomplete in two ways:
  1. AUDIT-006 scoped `cost_per_resolution`'s numerator to `execution_valid` observations (called `valid` in the code), but `per_task`'s own definition of a scored trial is `execution_valid AND passed is not None` - an execution-valid-but-unresolved evaluation (`passed=None`, an indeterminate verdict awaiting resolution, not yet a scored PASS/FAIL/CONTRACT_VIOLATION) was still counted, since `valid` alone didn't check for a resolved verdict. Reproduced directly: a $3 scored success alongside a single $200 execution-valid-but-unresolved observation reported `cost_per_resolution: 203.0` instead of `3.0` - functionally the same defect class as AUDIT-006's finding, just one population narrower than AUDIT-006's fix actually closed. Fixed by introducing `scored = [v for v in valid if v.passed is not None]` and using that (not `valid`) as the cost numerator's population, matching `per_task`'s own scored definition exactly.
  2. `total_campaign_cost_usd` (added in AUDIT-006) summed only engineer + development-application cost, excluding the separately-tracked verifier cost entirely - so a field named "total campaign cost" was not actually a total, understating real spend and potentially confusing a reader who takes the name at face value. Fixed by making it a genuine grand total (engineer + development-application + verifier cost, requiring all three known on every observation), while `verifier_cost_total_usd` remains its own separate breakdown alongside it, satisfying both halves of spec's "report verifier ... separately; publish total campaign cost" in one field each rather than one field standing in for both. There is no infrastructure-cost field on `TrialObservation` yet, so the total does not yet include it - a real, disclosed gap, not silently ignored.
- Consequence: `tests/test_analysis.py::test_cost_per_resolution_excludes_unresolved_valid_evaluations` reproduces the exact failure and is verified to fail against the pre-fix code; `test_cost_per_resolution_excludes_infrastructure_invalid_attempts` was updated to supply `verifier_cost_usd` on both observations (required now that `total_campaign_cost_usd` is a genuine grand total) and its expected total changed from `203.0` to `204.0` to include verifier cost. The full regression suite (101 tests) passes.
