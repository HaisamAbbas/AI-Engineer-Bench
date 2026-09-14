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

## ENG014-004 - Defer TypeScript client generation until apps/web exists

- Date: 2026-09-14
- Status: accepted
- Decision: `scripts/generate_openapi.py` regenerates `docs/implementation/evidence/ENG-014/openapi.json` reproducibly, satisfying "generate OpenAPI ... artifacts." Generating a TypeScript client from it is deferred until `apps/web` exists (ENG-016); a client with no consumer would be premature scaffolding, consistent with BOOT-003's decision to defer `apps/web` until its own ticket.
- Consequence: when ENG-016 begins, it generates the TS client from this same checked-in OpenAPI schema rather than duplicating API definitions by hand.

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

- Date: 2026-09-14
- Status: accepted
- Decision: a second independent review of ENG014-005/006 found the corrupt-manifest guard was ported to `registry.py` but not to the equivalent `TaskRevision`/`EntrantRevision.model_validate` calls inside `freeze()` (same bug, sibling call site); that two concurrent freeze calls sharing the same Idempotency-Key both pass `check_or_reserve` before either commits, so the atomic-UPDATE loser fell into the `conflict()` branch and returned 409 to what was actually a legitimate retry, instead of `finalize()`'s replay path; and that freeze's atomic guard checked `state='draft'` but not `revision`, so a PATCH committing between freeze's initial read and its final write would be silently discarded rather than detected. Fixed by: extracting `revisions.validate_stored_manifest` and using it at both call sites; re-running `check_or_reserve` in the freeze UPDATE's zero-rowcount branch before concluding a real conflict, since the loser's failure may just be the winner's own commit becoming visible under READ COMMITTED; and capturing the draft's `revision` at freeze's initial read and including it in the final UPDATE's WHERE clause, mirroring `patch_campaign`. Also fixed two lower-severity items the same review raised: `session.get()` after a raw Core UPDATE relied on SQLAlchemy's `synchronize_session='evaluate'` to keep the identity-mapped object fresh, which is version-dependent - both `patch_campaign` and `freeze` now use `.returning(CampaignRow)` to get the authoritative post-write row directly from Postgres; and `idempotency.finalize`'s `except IntegrityError` assumed the cause was always the idempotency unique constraint, which would crash with an unhandled `NoResultFound` on any other constraint violation in the same transaction - it now checks `exc.orig.diag.constraint_name` and re-raises anything that isn't `uq_idempotency_scope_key`.
- Consequence: the same-key-retry race, the revision-blind freeze guard, and the sibling corrupt-manifest call site are each covered by a dedicated test using real thread concurrency or direct fault injection (`test_concurrent_freeze_same_idempotency_key_replays_not_409`, `test_freeze_detects_concurrent_patch_and_does_not_silently_drop_it`, `test_freeze_with_corrupt_referenced_manifest_is_503_not_500`, `test_finalize_reraises_unrelated_integrity_error`), not just asserted fixed by inspection.
