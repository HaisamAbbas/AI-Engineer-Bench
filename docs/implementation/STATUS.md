# Engineering status

Updated: 2026-09-18
Prompt 14 update: ENG-011, ENG-017, and ENG-018 are COMPLETE following independent technical review on 2026-09-18. The verified implementation covers the frozen-plan aggregation prerequisite, campaign administration and budget reservation, publication/review/signing/export, retained-candidate regrade, corrections and supersession, generated clients, authenticated browser administration, and the full local fixture publication journey. ENG-017's specified state-rule acceptance and ENG-018's PUB-01/PUB-02 plus redaction acceptance are demonstrated in `evidence/ENG-017/campaign-admin.md`, `evidence/ENG-018/prompt14-verification.md`, and `evidence/ENG-018/review-closure.md`. This is an independent technical acceptance, not a claim of human organizational independence or an official release. Production OIDC/JWKS validation, two-human official-publication approval, hardened isolation/Harbor, deployment CI, and public release remain later-ticket or operational gates. Protocols requiring trace coverage continue to fail closed until a trusted trace-completeness contract exists.


Current phase: ENG-011 and ENG-015 through ENG-018 are COMPLETE. The typed-client artifact has been regenerated and both declared artifact checks pass. See `evidence/ENG-011/aggregation-review.md` and `evidence/ENG-018/review-closure.md`. No real public release was performed or authorized.
Scores/evaluations: no benchmark scores; local development-admission checks only

## Status vocabulary

- `READY`: dependencies are satisfied and the ticket can be started.
- `NOT_STARTED`: not started; dependencies may be satisfied, but a prior requested phase remains next.
- `BLOCKED`: one or more listed engineering dependencies are incomplete.
- `IN_PROGRESS`: implementation has begun but acceptance is not met.
- `COMPLETE`: every acceptance gate passed and evidence exists at the recorded path.

Planned evidence paths below are destinations, not claims that evidence exists. A ticket becomes `COMPLETE` only after its acceptance behavior is demonstrated and the path is updated to actual immutable or reviewable evidence.

## Backlog

| Ticket | Deliverable | Depends on | Status | Acceptance gates | Evidence path |
| --- | --- | --- | --- | --- | --- |
| ENG-001 | Toolchain and Harbor compatibility spike | None | BLOCKED | Pin tested Python, Harbor, and package versions; prove one installed agent, multi-service editing, deadline stop/collection, allowed new-file collection, clean external replay, teardown/control capability, and cost/trace coverage disclosure. Deterministic contract gates pass; real installed coding-agent smoke is not run because provider/model use was not authorized. | `docs/implementation/evidence/ENG-001/compatibility-report.md` and `deterministic-run-summary.json` |
| ENG-002 | Core schemas and canonical IDs | None | COMPLETE | CT-01: YAML formatting does not change canonical digest. CT-02: unknown fields and unresolved image digests fail before dispatch. Generate versioned JSON Schemas and golden vectors. Core planner resolves/freeze-validates exact trial matrices. | `docs/implementation/evidence/ENG-002/verification.md`, `schemas/`, and `tests/test_core_contracts.py` |
| ENG-003 | Local artifact store and safe extraction | ENG-002 | COMPLETE | EX-01 retains allowed untracked files. SE-01 rejects escaping symlinks. Validate sizes, paths, file types, manifests, and content integrity. A review found path-safety checks (`_safe_relative`, `CandidateFile.path`, `SubmissionPolicy` include/protected) rejected `..` and leading `/` but not a Windows drive-qualified path (`C:/outside`), which discards the destination root entirely when joined on Windows (AUDIT-002) — fixed at all three call sites; `safe_extract_tar` also now verifies every resolved target stays under its destination root. | `docs/implementation/evidence/ENG-003/artifact-store.md` and `tests/test_candidate_artifacts.py` |
| ENG-004 | RAG-01 application and public contract | ENG-002 | COMPLETE | Reproducible intentionally broken baseline; public ticket and API contract complete; all hidden checks map to public requirements. | `suites/dev/rag.document-freshness/` and `docs/implementation/evidence/ENG-004-005/admission-report.md` |
| ENG-005 | RAG-01 trusted verifier, reference, and counterexamples | ENG-004 | COMPLETE | EV-01 baseline fails intended checks; EV-02 reference and alternative pass; EV-03 shortcuts fail; ten clean fixture resets succeed. Independent human review remains pending for official admission. | `tests/maintainer/rag01/`, `scripts/run_rag01_admission.py`, and `docs/implementation/evidence/ENG-004-005/admission-report.json` |
| ENG-006 | Executor adapter and deadline protocol | ENG-001, ENG-003 | COMPLETE | EX-02 freezes artifact at deadline after stopping owned engineering processes, records reason/attribution, preserves evidence, and verifies allocation cleanup. A review found any bare `RuntimeError` from an evaluator was blamed on the candidate (`CANDIDATE_RUNTIME_FAILURE`), when only a dedicated `CandidateUnavailableError` should mean that (AUDIT-002) — fixed; a bare `RuntimeError` now correctly falls through to `SCORER_ERROR`. Real installed-agent validation and official isolation remain blocked. | `packages/aieb-runner/src/aieb_runner/lifecycle.py`, `tests/test_attempt_lifecycle.py`, and `docs/implementation/evidence/ENG-006-007/vertical-lifecycle.md` |
| ENG-007 | Fresh candidate build and replay | ENG-003, ENG-005, ENG-006 | COMPLETE | Replays allowed submitted artifacts over pristine RAG-01 base in a new build allocation and evaluates live candidate code externally; no engineering allocation, process, or state is reused. Official isolation remains blocked. | `packages/aieb-runner/src/aieb_runner/lifecycle.py`, `tests/test_attempt_lifecycle.py`, and `docs/implementation/evidence/ENG-006-007/vertical-lifecycle.md` |
| ENG-008 | Role-separated usage and caps | ENG-006 | COMPLETE | Separate role ledger deduplicates broker/adapter receipts, preserves unknown/lost billing, and distinguishes physical retries. Hard-cost enforcement is explicitly unavailable without provider reservations. | `packages/aieb-runner/src/aieb_runner/accounting.py`, `tests/test_accounting_and_cli.py`, and `docs/implementation/evidence/ENG-008-009/local-cli.md` |
| ENG-009 | CLI planner, run, inspect, and report | ENG-002, ENG-007, ENG-008 | COMPLETE | Local RAG-01 validate/plan/run/resume/inspect/report path writes frozen JSON/JSONL state and static HTML; normal failed tasks are data, not CLI crashes. | `packages/aieb-cli/`, `tests/test_accounting_and_cli.py`, and `docs/implementation/evidence/ENG-008-009/local-cli.md` |
| ENG-010 | EXT-02 and TOOL-01 vertical tasks | ENG-005, ENG-009 | COMPLETE | Both new families pass baseline/reference/alternative/shortcut/reset admission gates and reference repairs run through validated fresh CLI path. Independent review remains pending. | `suites/dev/ext.batch-alignment/`, `suites/dev/tool.false-completion/`, and `docs/implementation/evidence/ENG-010/admission-report.md` |
| ENG-011 | Analysis and coverage eligibility | ENG-002, ENG-009 | COMPLETE | Independently accepted after aggregation review: replacement retention, unknown accounting/deadlines, validity corrections, and frozen-manifest membership fail-closed behavior are verified; evidence is `docs/implementation/evidence/ENG-011/aggregation-review.md`. The closure statements below are historical and superseded. ST-01 zero-success cost is undefined; ST-02 incomplete plans cannot produce a canonical complete rank; ST-03 preserves project/family clustering; no fabricated intervals. A review found `summarize()` could not detect a wholly-missing planned cell (only under-repeated existing cells); fixed via an optional `planned_cells` check (AUDIT-001). A further review found `summarize()` emitted only one combined suite-wide rate, with no per-entrant or per-category rate, and folded verifier cost into (or silently omitted it from) `cost_per_resolution` rather than reporting it separately as spec section 30 requires — fixed via `per_entrant`/`per_category` outputs (derived from the same per-task cells) and a standalone `verifier_cost_total_usd` (AUDIT-003). A second review found that first `per_category` implementation blended every entrant's rate together into one category number - fixed so `per_category` is `{category: {entrant: rate}}` (AUDIT-004). A third, independently reproduced review found `complete_for_rank` counted raw observations per cell, not valid/resolved ones - a cell with `required_repetitions` raw attempts but an infrastructure-invalid one among them (not a scored outcome) reported complete with a smaller `n` instead of incomplete; confirmed by direct reproduction, then fixed by deriving the completeness count from the same per-task valid-observation count (`n`) `per_task` already computes, not `len(values)` (AUDIT-005). Downgraded from COMPLETE: no caller yet wires a frozen campaign's actual plan/category map through `planned_cells`/`category` end to end, so a wholly-missing planned cell still cannot be detected in real aggregation, only in tests that supply it explicitly - this is a real integration gap, not only a documentation one. A fourth, independently reproduced review found `cost_per_resolution`'s numerator summed every observation's cost regardless of `execution_valid`, so one expensive infrastructure-invalid attempt inflated the reported per-resolution cost despite never being scored (reproduced: a $3 valid success plus a $200 infrastructure-invalid attempt reported `203.0` instead of `3.0`) - fixed by scoping the numerator to scored (valid) observations, matching the denominator, and adding a separate `total_campaign_cost_usd` field (every attempt, valid or invalid) for the spec's distinct "publish total campaign cost including invalid attempts" requirement (AUDIT-006). A fifth, independently reproduced review found that fix still counted an execution-valid-but-unresolved observation (`passed=None`, no verdict yet) in the cost numerator, since "valid" alone wasn't the same population `per_task` calls scored (`execution_valid AND passed is not None`) - fixed by introducing that exact `scored` population for the cost numerator. The same review found `total_campaign_cost_usd` excluded verifier cost entirely despite its name, understating the real total - fixed to be a genuine grand total (engineer + dev-application + verifier cost); no infrastructure-cost field exists yet on `TrialObservation`, so the total still doesn't include one, a disclosed gap (AUDIT-007). Re-promoted to COMPLETE: the outstanding integration gap is closed - `services/api/src/aieb_api/aggregation.py::aggregate_campaign_snapshot` is a real caller that derives a frozen campaign's OWN planned (task, entrant) cells and per-task categories from `campaign.resolved` and wires them through `summarize(planned_cells=...)`/per-observation category, so a wholly-missing planned cell is now detected as incomplete coverage in real aggregation (not only in tests that supply `planned_cells`); covered by `tests/test_api_service.py::test_aggregate_campaign_snapshot_flags_a_wholly_missing_planned_cell_as_incomplete` and `::test_aggregate_campaign_snapshot_is_complete_and_categorized_when_every_planned_cell_is_present` against real PostgreSQL. ENG-018's publication flow consumes this function. A per-ENTRANT valid-vs-planned coverage count remains a disclosed non-field (the snapshot's `limitations` says so). | `packages/aieb-analysis/`, `services/api/src/aieb_api/aggregation.py`, `tests/test_analysis.py`, `tests/test_api_service.py`, and `docs/implementation/evidence/ENG-011/analysis.md` |
| ENG-012 | Eighteen-trial development pilot | ENG-010, ENG-011 | BLOCKED | Offline 3-task x 2 deterministic-fixture entrant x 3 repetition matrix is frozen. Real pilot is not run: no explicit provider/cloud authorization, credentials, or approved cap. Re-examined during AUDIT-003 triage: `development-pilot-18.json`'s own `state: "prepared-not-authorized"` and `execution_blocker` fields, and this row's own BLOCKED status, already say this honestly — it is not presented anywhere as a resolved campaign, so no change was needed. | `examples/development-pilot-18.json` and `docs/implementation/evidence/ENG-012/pilot-preparation.md` |
| ENG-013 | Remaining nine tasks and independent reviews | ENG-012 | IN_PROGRESS | All twelve public development tasks, including TOOL-02's complete post-fix matrix, have passing local baseline/reference/alternative/shortcut and ten-reset fixture evidence. All twelve have explicit CLI runtime mappings and twelve distinct synthetic family IDs. A review found `aieb task validate` never actually validated `TaskRevision` (AUDIT-001), letting five of twelve task.yaml files carry genuine schema defects (unquoted digest strings, invalid `category`/`egress_policy` enum values) undetected; the CLI now validates for real and all five were corrected - confirmed by running `aieb task validate` against all twelve tasks. A further review found every task's `repository_digest`/`provenance_digest`/`contract_digest`/`service_topology_digest`/`evaluator_digest` was a repeated-digit placeholder that never reflected any actual content (AUDIT-003) - `scripts/compute_task_digests.py` now derives each from the real on-disk repo/provenance.json/contract/environment-doc/evaluator source, and `aieb task validate` recomputes and rejects a mismatch (`tests/test_accounting_and_cli.py::test_stale_content_digest_fails_validation`), so a stale value can no longer pass silently; `environment.official_image`'s digest remains an explicit placeholder since no image has ever been built or pushed, and is not currently used to dispatch anything (local runs execute `entrypoint` directly, never pull or run this image reference). A second review found `evaluator_digest` hashed only the single `evaluator.py` file, missing behavior in code it imports (`tests/maintainer/common.py`, used by 11 of 12 evaluators, and rag01's own sibling `fixture.py`) - fixed via `hash_evaluator_closure`, hashing the evaluator's full trusted package directory plus `common.py`; all twelve task.yaml files were regenerated again (AUDIT-004). TOOL-02's fixture previously always returned success immediately, so its "ambiguous-retry-resolves" check never exercised genuine ambiguity; the fixture now commits the write and drops the acknowledgment on the first attempt per identity, and baseline/reference/alternative/counterexample backends were corrected to genuinely retry (or genuinely fail to) under that real uncertainty (AUDIT-003). The "twelve distinct family IDs" claim was independently re-examined: each task's `backend.py` does encode a genuinely different bug/domain (not renamed copies), though several are very thin (2-4 lines of logic) inside a shared, intentionally generic HTTP-server harness - a real distinction in what is tested, not a fabricated one, but a shallow one; documented as-is rather than papered over. A second review found the checked-in `admission-report.json` still showed TOOL-02's pre-fix behavior (baseline resolving ambiguity, `never-retry` duplicating the effect) - never regenerated after the fixture fix; added `scripts/generate_eng013_admission_report.py` and regenerated it to match the corrected fixture's live output (AUDIT-004). Independent reviews are pending for every task; therefore an admitted-only frozen release manifest cannot be created. ENG-012 remains blocked but does not prevent public-task authoring. | `suites/dev/catalog.json`, `tests/test_eng013_admission.py`, `docs/implementation/evidence/ENG-013/local-admission.md`, and `docs/implementation/evidence/ENG-013/admission-report.json` |
| ENG-014 | API auth, persistence, and migrations | ENG-002 | COMPLETE | API-01 returns 409 for reused idempotency key with changed body; API-02 hides unauthorized private artifact refs as 404; migration compatibility (upgrade/downgrade/upgrade) tested against a real disposable PostgreSQL instance. A review found role checks trusted a bearer token's `aieb_roles` claim directly, never consulting the persisted `role_bindings` table (AUDIT-001) - fixed: `resolve_roles` is now the sole source of authorization, and a related latent bug (artifact ownership compared a raw OIDC subject string against an internal UUID, so it could never match) was fixed alongside it. A further review found no persistence-level enforcement of frozen-revision immutability (Prompt 11 explicitly required it "in persistence, not only in UI checks") - fixed via `BEFORE UPDATE` triggers on task/evaluator/entrant/fixture revision tables and on a frozen campaign's manifest fields, verified to block a direct `psql` UPDATE outside the ORM entirely, while still permitting legitimate campaign state transitions (AUDIT-002). Publication-endpoint status/visibility gating was re-examined and found not currently exploitable (only `published`/`withdrawn`/`superseded` exist, all meant to stay public). A further review found nothing recomputed `snapshot_digest` against the stored `snapshot` JSONB before serving it as canonical public results (AUDIT-003) - fixed via a `BEFORE UPDATE` trigger making the snapshot/digest pair immutable at insert time (mirroring the AUDIT-002 revision triggers, while still allowing `status` to transition published/withdrawn/superseded), plus an application-level recompute-and-compare on every read that serves a snapshot, returning `503 service_unavailable` on a mismatch instead of silently serving corrupted results. A second review found `resolve_roles` ignored `role_bindings.scope` entirely, so a role bound to one campaign/suite/test scope silently satisfied any global check - fixed via a `GLOBAL_SCOPE` sentinel and a `scope` parameter on `resolve_roles`/`require_role` (AUDIT-004). That same review found Prompt 11's required typed TypeScript client artifact had been deferred to ENG-016 rather than generated now (ENG014-004) - superseded: `scripts/generate_typescript_client.py` generates `docs/implementation/evidence/ENG-014/api-client.d.ts` from the checked-in OpenAPI schema today, independent of whether `apps/web` exists yet to consume it. Campaign start/pause/resume/cancel, reviews, and publication writes remain ENG-017/ENG-018; worker leasing protocol remains ENG-015. | `services/api/`, `tests/test_api_service.py`, `tests/test_api_auth.py`, and `docs/implementation/evidence/ENG-014/api-service.md` |
| ENG-015 | PostgreSQL worker leasing and reconciliation | ENG-006, ENG-014 | COMPLETE | The real PostgreSQL migration/backfill and the relevant regression suite pass locally on Windows; lifecycle regressions cover process-tree cancellation, bounded JSON results, and killable BUILD. The Ubuntu/PostgreSQL workflow (`.github/workflows/eng015-verification.yml`) now passes green on Unix (run on commit `10bb063`): the same database, migration round-trip, worker-leasing, and lifecycle gates - including Unix process-group termination and the large-result VERIFY channel drain - plus the website build/tests and a live headless-Chrome browser check against the real API. Getting there required three CI fixes demonstrated only once the Unix gates actually ran (they had never run: a stale `astral-sh/setup-uv` pin failed every workflow at setup): (1) an out-of-sync `apps/web` npm lockfile; (2) two Unix-only failures - `test_api_migrations` hardcoded the Windows venv path (now `sys.executable`), and VERIFY concluded "no result" before draining bytes still buffered in the Unix socketpair when a large result's writer had exited (now drains to completion); (3) the browser-seed step colliding with rows the regression step left in the shared database (now resets the schema before seeding). | `services/api/src/aieb_api/worker/`, `packages/aieb-runner/src/aieb_runner/lifecycle.py`, `tests/test_attempt_lifecycle.py`, `tests/test_api_migrations.py`, and `.github/workflows/eng015-verification.yml` |
| ENG-016 | Public website and comparison views | ENG-011, ENG-014 | COMPLETE | Publication manifest binding, complete Run Evidence states/content, candidate and ticket integrity, and ticket-inclusive task bundle. Two independent review passes were remediated and accepted: (1) seven findings - a strict included/excluded tagged union so an included published selection must pin a complete non-null identity; a snapshot-digest binding tying the evidence manifest to the published snapshot (prevents undetected post-publication snapshot swap; it does NOT prove the snapshot's rates were derived from the selected evaluations - that semantic derivation check is deferred to ENG-018); publication restricted to terminal, scored, completed-campaign attempts; whitelist-redacted public trace events; public usage sourced only from the immutable evaluation, never mutable usage receipts; a lease-fenced `phase.started` trace write; and keyboard-operable ARIA tabs. (2) one follow-up blocker - a publishable attempt's `terminal_status` must be a real scored verdict AND equal the pinned evaluation's verdict (rejects `scorer_error`, and a `fail` attempt paired with a `pass` evaluation). Full real-PostgreSQL API regression (54 tests, incl. two terminal-status regressions), worker-leasing and lifecycle suites, and 32 frontend tests (incl. a Run Evidence keyboard-nav test) plus production build pass. Committed at `beb4bb4`. Semantic snapshot-derivation verification is explicitly ENG-018 work. | `apps/web/`, `services/api/src/aieb_api/`, `docs/implementation/evidence/ENG-016/website.md`, and `docs/implementation/evidence/ENG-016/browser/` |
| ENG-017 | Admin campaigns and budget reservations | ENG-008, ENG-015, ENG-016 | COMPLETE | Start/freeze/pause/resume/cancel rules, reservations, progress, authoritative web controls, campaign list/draft reads, invalidity-review writes, completion concurrency, and budget compatibility are implemented and independently technically reviewed. Production deployment validation remains ENG-020 work. | `docs/implementation/evidence/ENG-017/campaign-admin.md` and `docs/implementation/evidence/ENG-018/review-closure.md` |
| ENG-018 | Publication, redaction, and corrections | ENG-011, ENG-014, ENG-016 | COMPLETE | PUB-01 self-approval rejection, PUB-02 append-only withdrawal/correction with preserved notices, immutable selected evidence, pinned aggregation, signing, atomic exposure, redacted exports, regrade/supersession, mutation replay, and authenticated review UI are implemented and independently technically reviewed. A protocol requiring `required_trace_coverage` fails closed until a trusted completeness contract exists. Two-human official approval and actual publication remain ENG-022 gates; no public release was performed or authorized. | `docs/implementation/evidence/ENG-018/prompt14-verification.md` and `docs/implementation/evidence/ENG-018/review-closure.md` |
| ENG-019 | Official sandbox and threat-model tests | ENG-007, ENG-015 | IN_PROGRESS | SE-02 (deny-by-default, now token-authenticated egress + cloud-metadata denial, logged) and SE-01 (cited, ENG-003) pass at the application/container level against the existing Harbor Docker backend. Third review round closed two remaining bypasses fail-closed: the bind-mount scan now resolves Compose interpolation (`.env` + process env, `${VAR}`/operators), walks `include:`/`extends:` graph references, refuses unknown-YAML-tag/unparsable files and residual-`$`/required-but-unset sources, and the launch guard now checks the task's EFFECTIVE network plan via Harbor's own resolver (post-`extra_allowed_hosts` merge), refusing `PUBLIC`-effective or metadata-allowlisting phases before any Docker/Harbor call (`tests/test_eng019_sandbox_threat_model.py`, 39/39 green). Per-attempt scoped credentials + candidate/verifier identity separation (spec §37) are implemented end to end (repository: attempt- and role-fenced issuance, stale-revoke fencing so a delayed finally cannot kill a replacement's rotated token, reconciler sweep covering `engineering`/`verification`/`regrade`; `GET /v1/attempts/{attempt_id}/candidate` is verifier-ROLE-ONLY (`_require_role`: 401 absent/invalid, 403 valid-but-wrong-role) and returns the FULL stored payload; verification issues its verifier credential before any candidate read and revokes it on every infra-abort path). Two independent codex review rounds of the credential path wrote negative controls that re-opened premature "closed" claims; all are now closed: (1) issuance attempt/role-blind -> fenced by work-item type and attempt; (2) stale finally-revoke -> fenced to the credential row's own lease identity, rotated tokens survive; (3) reconciler missed crashed regrade -> sweep covers regrade and revokes both roles; (4) candidate capability unconsumed + candidate-role readable -> verifier-only, full payload, verification reads the candidate through the same credential gate; (5) import-time env leak -> evaluator delivered as `(module, qualname)` identity STRINGS (never pickled) and resolved only after `os.environ` is scrubbed, with allowlisted-value credential stripping (`?password=/token=/key=/secret=`); (6, reviewer blocker) `runner_bridge` still imported the evaluator in the WORKER PARENT -> identity (incl. qualname) now lives IN `TASK_RUNTIMES` and threads as plain strings, `run_verification` takes `evaluate_identity` directly, `runner_bridge` no longer imports the module at all, and the isolated child is the FIRST process to import it, after the scrub. Negative controls plant worker secrets BEFORE any probe import and drive the real production path (identity strings; end-to-end leased execution), asserting the probe never enters the worker parent's `sys.modules` and its import-time snapshot saw no secrets (asserted red, then reverted, against the reintroduced parent import). Suites green against the disposable `aieb-test-postgres` container (postgres:16, `postgresql+psycopg`): credentials 14/14, lifecycle 21/21, worker leasing 39/39, sandbox threat-model 39/39. GAP 3 IS CLOSED: the reviewer's deciding run on `895814a` accepted per-attempt scoped credentials + candidate/verifier identity separation for the currently supported architecture (credential+worker-leasing 53/53, lifecycle+sandbox 60/60, working tree + whitespace clean; hosted worker never imports evaluator modules in its parent, isolated child imports only after the scrub, all negative controls pass), with official VM/live-infrastructure validation explicitly deferred. Gaps 4 (restore-drill fencing), 5 (kill-switch API/CLI), and 6 (Prometheus metrics/alerts) remain open in order. Cross-trial access is explicitly disclosed as untested (needs live multi-container orchestration not built here). "Official VM provider" and "Harbor public-egress/metadata adversarial validation" remain Deferred - this is not a claim of VM-equivalent hardened isolation. | `docs/implementation/evidence/ENG-019/sandbox-review.md` |
| ENG-020 | Staging/production CI/CD, backups, and runbooks | ENG-014, ENG-015 | IN_PROGRESS | Worker draining (SIGTERM/SIGINT wired to the existing stop_event), auto-pause (per-campaign, 3 consecutive infra failures, requires acknowledged resume) and the global kill switch (dispatch-wide, distinct mechanism, torn down for paused campaigns too) are implemented and tested against real PostgreSQL. Migration rollback drill (representative dataset + old-code/new-schema compatibility) and a real pg_dump/pg_restore backup/restore drill (FIVE post-restore assertions) pass. Frozen-campaign toolchain pinning across a simulated software upgrade is tested. Exact ordered staging validation steps (deploy/migrate/verify/smoke/rollback) are written. New `sandbox-integration.yml`/`release-candidate.yml`/`dependency-review.yml` CI workflows add least-privilege `permissions:`, SBOM generation, and a gated (non-executable without cloud authorization) live-smoke placeholder; Python lint/type-checking CI is explicitly disclosed as not implemented (400+ pre-existing findings repo-wide; not introduced this late without triage). Real staging/production deployment, real OIDC, and a real live smoke test remain blocked on cloud authorization. Gap 4 (restore-drill pre-reconciliation fencing hole) is IMPLEMENTED as a system fence epoch committed at `f1000a6`; deciding-review findings 1 (FOR SHARE atomicity) and 2 (credential status epoch) were fixed at `27b14be`; re-review round 2 on `27b14be` found the operator command's barrier check was not atomic with the epoch bump (BLOCKING) plus two mediums (phony "operator-role" auth claim; drill bypassed the command) - all three fixed and pushed at `8ea56af` (`advance_fence_epoch_with_barrier` makes the barrier and bump ONE transaction; the command reports `current_user` and `--by-user` is an informational audit label; the drill drives the command as a subprocess). Re-review of `8ea56af` accepted all of that but found a NEW blocker in the concurrent-operator path: two concurrent `scripts/fence_advance.py` invocations each read epoch 0 up front (unlocked), so PostgreSQL serialized them (A committed 0 -> 1, B committed 1 -> 2) and B then REPORTED FAILURE AFTER ITS OWN MUTATION HAD COMMITTED (`fence-advance FAILED: expected epoch 0 -> 1, observed 2`, exit 1) - the same dangerous property this epoch exists to remove (a retry would double-advance). The round-3 fix, reproduced red first, then closed: (previous, new) epoch are BOTH read under the same locks and RETURNED by `advance_fence_epoch_with_barrier`; the CLI no longer does an unlocked pre-read or a post-commit mismatch check, `--check` is genuinely write-free (reads the fence row directly instead of the repository self-heal), and `--by-user` is validated against `users` with a clean REFUSED instead of a raw IntegrityError traceback. The reviewer's exact two-`_advance()` reproduction is now a regression test that drives the real script (red on `8ea56af`, green on the fix), the repository-seam pair-return contract is regression-tested, and ordering-B became a deterministic lock-timeout test. Verification on the round-3 tree: worker-leasing 46/46, credentials 15/15, backup/restore drill 5/5 through the command, by-user refusal clean at exit 3 with no epoch change. Gap 4 is ACCEPTED (final re-review of `049d824`: re-verified concurrent transitions 0->1/1->2 with no post-commit failure, atomic barrier/deactivation, invalid `--by-user` clean-refuses at exit 3 with the epoch unchanged, worker-leasing + credentials 61 passed, drill 5/5 through the real CLI, `HEAD == origin/main`, clean diff) and recorded COMPLETE. The acceptance review's two non-blocking follow-ups are logged and fixed here: (a) `--check` FAILS CLOSED when the `system_fence` singleton row is missing - it previously reported `fence epoch 0` and, with an active barrier, exited 0 even though the advance would fail; now it reports `UNKNOWN (system_fence row missing)` and exits 3, and the mutating form fails cleanly at exit 2 naming the row instead of tracing back (regression: `test_check_fails_closed_when_fence_row_missing_even_with_an_active_barrier`); (b) the stale ledger wording this sentence fixes. Gap 5 (operator kill-switch API/CLI - the repository knob exists, the operator surface did not) is now COMPLETE and ACCEPTED: `routes/kill_switch.py` (`GET /v1/kill-switch`, `POST /v1/kill-switch/activate`, `POST /v1/kill-switch/deactivate` with `administrator` role authorization, REQUIRED `Idempotency-Key` on all mutating routes (enforced at the FastAPI header level), and the existing `repository.activate_kill_switch`/`deactivate_kill_switch`/`is_kill_switch_active` functions under their FOR UPDATE singleton lock), and `scripts/kill_switch.py` (the operator CLI mirroring `fence_advance.py` with the same honest authorization statement, `--check` read-only pre-flight, and `activate`/`deactivate` subcommands). Repository functions now support `commit=False` for transactional atomicity with the idempotency record via `finalize()` (API-01); `activate_kill_switch` raises `ValueError` if already active (atomic check under the FOR UPDATE lock, no separate unlocked pre-read); `deactivate_kill_switch` returns `bool` and handles the already-inactive case atomically. `KillSwitchRequest.reason` now requires a non-blank value via `field_validator`. Registered the route in `app.py`. Tests: 16 new kill-switch API tests in `tests/test_api_service.py` covering status-read (operator/reviewer/admin roles), 403 for non-administrator mutate, 409 conflict when already active, deactivate idempotency, idempotency-key-required enforcement on all mutations, empty/whitespace reason rejection, idempotent replay with same key, conflict replay after first activation, and deactivate-then-activate cycle; verified against the disposable `aieb-test-postgres` container (16/16 passed, full 96-test API suite green, 50/50 worker-leasing suite green including 2 existing kill-switch behavior tests). Gap 6 (Prometheus /metrics + alerting) is now COMPLETE: a hand-rolled Prometheus text-exposition-format exporter (`worker/metrics.py`'s `render_prometheus_text()`, no new dependency - `prometheus_client` is not installed and not added) backs a new authenticated `GET /metrics` (`routes/metrics.py`, same `require_role("operator","reviewer","administrator")` gate `kill_switch.py`'s GET route uses). `worker/metrics_queries.py` computes the DB-truth gauges (kill-switch state, per-campaign consecutive infrastructure failures, worker heartbeat age, active budget reservation age) fresh at every scrape; `aieb_reconciler_worker_artifacts_purged_total` and `aieb_attempt_infrastructure_invalid_total` are real in-process counters incremented at their call sites in `reconciler.py`/`repository.py`. `deploy/alerts/prometheus-rules.yml`'s header is corrected to say the exporter now exists (still no deployed Prometheus/Alertmanager/paging pipeline anywhere). `tests/test_metrics.py` (10/10 passed) includes a regression test parsing every alert rule's `expr:` for its metric names and asserting each appears in a real scrape, plus functional tests proving the kill-switch, infrastructure-failure, infrastructure-invalid, and purge instrumentation is real, not decorative. Full regression re-run clean: `tests/test_api_service.py` 101/101, `tests/test_worker_leasing.py` 47/47. | `docs/implementation/evidence/ENG-020/operations-review.md`, `deployment-topology.md`, `staging-validation-steps.md`, and `runbooks.md` |
| ENG-021 | Official holdout curation and protocol review | ENG-013 | IN_PROGRESS | Family split, provenance/contamination review, evaluator review, and sample-size decision are complete at the software-preparation level. Real holdout curation and independent admission review remain PENDING; no real campaign executed. | `docs/implementation/evidence/ENG-021/release-process-runbook.md`, `docs/implementation/evidence/ENG-021/campaign-execution-guide.md`, `docs/implementation/evidence/ENG-021/reviewer-checklist.md`, `docs/implementation/evidence/ENG-021/official-campaign-proposal.json`, `docs/implementation/evidence/ENG-021/prerequisites-audit.json`, `examples/proposed-release-candidate.json`, and `tests/test_eng021_bundle_exclusions.py`, `tests/test_eng021_family_split.py`, `tests/test_eng021_campaign_proposal.py` |
| ENG-022 | Official campaign and release | ENG-018, ENG-019, ENG-020, ENG-021 | BLOCKED | Complete prespecified cohort, required approvals, immutable publication/evidence, attrition disclosure, retention, and appeals process. | Immutable publication manifest (planned; no path assigned) |
| ENG-023 | Fixed reference model-track loop | ENG-009 | BLOCKED | P0 precondition (§18: "implement only after agent-track P0 works") not met — ENG-001 real installed-agent smoke is blocked on provider/model authorization. Codebase verification confirmed Track/ModelProfile/credential_ref_type/actor_role/BudgetEnforcement.ESTIMATED_TIME_LIMITED/agent_import_path already exist; the genuine remaining work is the model-provider reference coding loop itself, gated on P0. If prepared without authorization, use `state: "prepared-not-authorized"` + `execution_blocker` (mirroring ENG-012/development-pilot-18.json). | `docs/implementation/evidence/ENG-023/README.md` |
| ENG-024 | Model-track campaign | ENG-011, ENG-023 | BLOCKED | Separate model-track cohort (Track.MODELS) with fixed reference agent configuration and disclosed provider/control limitations. Model identities (engineer_model, application_model, verifier_judge_model) recorded in `usage_request` via existing `actor_role` ledger. No merge with agent-track scores. | Immutable model-track publication manifest (planned; no path assigned) |

## Release gates

| Gate | State | Required evidence |
| --- | --- | --- |
| P0 vertical slice | BLOCKED | ENG-001 through ENG-009 acceptance, including one real agent run and evaluator controls |
| P1 development preview | BLOCKED | Twelve admitted tasks, usable CLI docs, complete development campaign, immutable local artifacts |
| P2 public beta | BLOCKED | API/web parity, roles, immutable publication/corrections, redaction, backups, cancellation |
| P3 official release | BLOCKED | Hardened isolation, independent reviews, held-out protocol, complete campaign, retention and appeals |

## Bootstrap evidence

- Immutable source copies: `docs/specs/`
- Tool observations and provisional pins: `toolchain.json`
- Reproducible local checks: `scripts/dev.py`, `tests/test_dev_bootstrap.py`
- Bootstrap verification command: `./dev.ps1 check`
- Implementation decisions: `docs/implementation/DECISIONS.md`
- Continuation state: `docs/implementation/SESSION_HANDOFF.md`

## ENG-001 evidence

- Compatibility results and limitations: `docs/implementation/evidence/ENG-001/compatibility-report.md`
- Normalized deterministic run record and raw-output hashes: `docs/implementation/evidence/ENG-001/deterministic-run-summary.json`
- Executable adapter fixture: `tests/integration/test_eng001_harbor.py`
- Real installed-agent gate: `BLOCKED`; exact not-run command is in the report and session handoff

## ENG-002 evidence

- Contract and planner verification: `docs/implementation/evidence/ENG-002/verification.md`
- Generated versioned schemas: `docs/implementation/evidence/ENG-002/schemas/`
- Golden vectors and explicitly non-result examples: `docs/implementation/evidence/ENG-002/examples/`
- Behavioral tests: `tests/test_core_contracts.py`

## ENG-003 evidence

- Artifact layout, invariants, and results: `docs/implementation/evidence/ENG-003/artifact-store.md`
- Behavioral tests: `tests/test_candidate_artifacts.py`

## ENG-004 and ENG-005 evidence

- Public development task, visible API contract, baseline, and candidate repairs: `suites/dev/rag.document-freshness/`
- Maintainer-only held-out fixture generator and external HTTP evaluator: `tests/maintainer/rag01/`
- Reproducible fresh-environment admission runner: `scripts/run_rag01_admission.py`
- Observed matrix and ten-reset result: `docs/implementation/evidence/ENG-004-005/admission-report.md` and `admission-report.json`
- Independent human review: `PENDING`; this is not an official task admission or benchmark release.

## ENG-006 and ENG-007 evidence

- Deadline-first local lifecycle and explicit attribution: `packages/aieb-runner/src/aieb_runner/lifecycle.py`
- Behavioral interruption, replay, failure, and cleanup tests: `tests/test_attempt_lifecycle.py`
- Limitations, exact commands, and observed deterministic outcomes: `docs/implementation/evidence/ENG-006-007/vertical-lifecycle.md`
- Real installed-agent RAG-01 smoke: `BLOCKED`; requires explicit provider/model authorization, credentials, and an existing cap.

## ENG-008 and ENG-009 evidence

- Role-separated receipt reconciliation and reservations: `packages/aieb-runner/src/aieb_runner/accounting.py`
- Local CLI package and entry point: `packages/aieb-cli/` (`aieb`)
- Behavioral local workflow tests: `tests/test_accounting_and_cli.py`
- Commands, exit behavior, and capability limitations: `docs/implementation/evidence/ENG-008-009/local-cli.md`

## ENG-010 through ENG-012 evidence

- EXT-02 and TOOL-01 packages, external evaluators, controls, and admission: `suites/dev/`, `tests/maintainer/ext02/`, `tests/maintainer/tool01/`, `docs/implementation/evidence/ENG-010/`
- Analysis primitives and behavioral tests: `packages/aieb-analysis/`, `tests/test_analysis.py`, `docs/implementation/evidence/ENG-011/analysis.md`
- Frozen but unexecuted pilot preparation: `examples/development-pilot-18.json`, `docs/implementation/evidence/ENG-012/pilot-preparation.md`

## ENG-020 Gap 5 final independent closure

The operator kill-switch API/CLI is accepted for the currently supported local/PostgreSQL
control plane. Its state transition and idempotency record commit atomically; an in-flight retry
with the same key replays rather than returning a false conflict; the API and CLI fail closed if
their migration-seeded singleton is missing; and the published OpenAPI and generated TypeScript
match the route surface. This does not close ENG-020: Gap 6 (Prometheus metrics and alerting),
real OIDC, cloud deployment, and live-smoke authorization remain open as recorded above.

## ENG-020 Gap 6 - Prometheus metrics exporter (COMPLETE)

`GET /metrics` now exists: a hand-rolled Prometheus text-exposition-format (0.0.4) exporter
(`worker/metrics.py::render_prometheus_text()`, no `prometheus_client` dependency added), gated
behind the same authenticated operator/reviewer/administrator role every other operator route in
this app already requires. Every metric name `deploy/alerts/prometheus-rules.yml`'s alert
expressions reference is really exported: `aieb_kill_switch_active`,
`aieb_campaign_consecutive_infrastructure_failures`, `aieb_budget_reservation_age_seconds`,
`aieb_worker_heartbeat_age_seconds`, `aieb_reconciler_worker_artifacts_purged_total`, and
`aieb_attempt_infrastructure_invalid_total`. The four DB-truth gauges are computed fresh from the
database at every scrape (`worker/metrics_queries.py`), not tracked as possibly-stale in-process
state; the two running-total counters are incremented at their real call sites
(`repository.py`'s two `terminal_status = "infrastructure_invalid"` sites,
`reconciler.py`'s purge count). `deploy/alerts/prometheus-rules.yml`'s header is corrected: the
exporter it assumed did not exist now does, in this repository, reachable via `GET /metrics` - no
Prometheus server, Alertmanager instance, or paging pipeline is deployed anywhere, and that
 remains explicitly out of scope. `tests/test_metrics.py` (12/12 passed, 9 subtests) covers the
auth gate, response shape/content-type, a regression test that every alert rule's metric name is
present in a real scrape, and functional proof that the instrumentation reflects real state
changes rather than being decorative. Full regression re-run clean:
`tests/test_api_service.py` 101/101, `tests/test_worker_leasing.py` 47/47. This closes the last
open ENG-020 gap; real OIDC, cloud deployment, and live-smoke authorization remain blocked on
cloud authorization/budget, as previously disclosed, and independent review of this closure
remains open per this repository's standing acceptance policy.

Post-closure corrections: `tests/test_metrics.py` is now 12/12 with regressions for missing
kill-switch state and cross-process durable counters. Worker counters persist in the shared
PostgreSQL `metric_counter` table, and exact `work_item.last_heartbeat_at` timestamps are written
on claim and heartbeat. Migration `c7d8e9f0a1b2` merges the existing migration heads and passed a
downgrade/upgrade round-trip.

## ENG-021 review follow-up (2026-09-21)

An independent review of the ENG-021 preparation package found it correctly left BLOCKED rather
than claiming release readiness, but flagged eight gaps. Three were genuinely fixable in
software/docs and are now closed; five remain blocked on real human/infrastructure/authorization
steps that cannot be closed from inside this repository, and are NOT closed here.

**Fixed:**

1. **Misleading "12/12 pass" overlap wording (was gap 3)**: `provenance-overlap-review.json`'s
   per-task `PASS` status previously absorbed a skipped `holdout-overlap` check without saying so
   at the top level, and `README.md` described the file as a flat "12/12 pass" for "provenance +
   token overlap" when the overlap half never ran (`"no --holdout-dir provided"` was the actual
   per-task detail, unchanged). `scripts/provenance_overlap_review.py` now emits explicit top-level
   `holdout_overlap_assessed: false` and `overlap_review_status` fields when no `--holdout-dir` is
   given, spelling out that overlap was NOT assessed and that no genuine held-out fixture directory
   exists yet. The JSON was regenerated by actually re-running the script (not hand-edited), and
   `README.md`'s table row now reads "12/12 pass" only for the checks that ran, with overlap called
   out as NOT ASSESSED. No holdout set was fabricated to make an overlap check pass — none exists,
   so none was run.
2. **Cost-number conflict between `official-campaign-proposal.json` and `campaign-execution-guide.md`
   (was gap 7)**: the guide's "~$39.12 (20% margin: ~$46.94)" could not be reconciled against the
   frozen proposal's `cost_reservation` block or against `scripts/generate_campaign_proposal.py`,
   which is the single source of truth for this number. The script computes cost over the
   540-trial replacement-inclusive basis (180 base trials × (1 + `max_replacements`=2)):
   $0.20/trial role cost + $0.10/trial environment cost = $0.30/trial × 540 = $162.00 reservation,
   ×1.20 margin = $194.40. The guide's stale figures were replaced with these authoritative,
   script-generated numbers and the reconciliation basis is now spelled out inline so the two
   documents cannot silently drift apart again.
3. **Bundle test Windows temp-directory failures (was gap 8)**: the review reported 6 of the
   ENG-021 bundle tests failing on Windows with temp-directory permission errors. This was NOT
   reproduced here: `tests/test_eng021_bundle_exclusions.py` (6/6) and the full ENG-021 suite
   (23/23) passed cleanly across three repeated runs each, via both `python -m unittest` and
   `pytest`, with no PermissionError observed. Rather than declare the gap closed on an
   unreproduced claim, the standard, well-documented Windows mitigation was applied defensively:
   both the test's `tearDown` and `build_release_bundle.py`'s own pre-build `rmtree` now clear a
   target's read-only bit and retry on `PermissionError` (`onerror` handler) instead of using
   `ignore_errors=True`, which would silently hide a real path leak. This is a genuine hardening
   fix for the exact class of error described (inherited read-only bits from `shutil.copy2`,
   transient antivirus/indexer file locks), not a guessed root cause dressed up as a fix — the
   original failure's precise cause remains undiagnosed because it did not recur in this
   environment.

**Still genuinely blocked (not addressed here — each requires a real human/infrastructure/content
step, not software):**

- **Gap 1 — no genuine held-out family**: all 12 tasks remain `official-public-origin`; no
  distinct held-out application packages exist in this repo or elsewhere that could be curated
  without relabeling variants of the same 12 tasks. Unblock requires: authoring genuinely distinct
  application packages (new base codebases, not variants) and running `scripts/curate_holdout.py`
  against them to an out-of-repo directory.
- **Gap 2 — no independent admission reviews**: `reviewer-checklist.md` remains 100% unchecked for
  all 12 tasks, as verified in this pass. Unblock requires: real independent human reviewers
  working through that checklist; this cannot be self-certified by the preparer or by an agent.
- **Gap 4 — no pilot-derived variance**: `official-campaign-proposal.json`'s sample-size table
  remains explicitly assumption-based (`note_critical` unchanged) because ENG-012 has not run.
  Unblock requires: authorized provider/model credentials and budget to execute the ENG-012 pilot,
  then regenerating the sample-size table from its real observed variance.
- **Gap 5 — insufficient project diversity**: the proposal still discloses only 3 of the 6 base
  application types spec section 28 expects (`statistical_protocol.clustering.caveat`, unchanged).
  Unblock requires: authoring 3 additional genuinely distinct base application projects, not
  relabeling existing ones.
- **Gap 6 — no authorization or real campaign**: confirmed no unauthorized campaign or publication
  occurred; STATUS remains ENG-021 IN_PROGRESS / ENG-022 BLOCKED as recorded in the backlog table
  above. Unblock requires: real provider credentials, spend approval, hardened VM validation
  (ENG-019), staging/production deployment (ENG-020), and live smoke authorization — none of which
  a software/docs pass can grant.

Overall status is unchanged by this pass: **ENG-021 remains IN_PROGRESS, ENG-022 remains BLOCKED.**
No official campaign or release is claimed or was performed.

## ENG-021 review follow-up, round 2 (2026-09-21)

A second independent review of the above pass found two of the three "fixed" items were not
fully closed, plus a wording discrepancy in the handoff summary. All three are addressed here.

**Fixed:**

1. **A fourth stale cost figure was found**: `release-process-runbook.md` line 91 still stated
   "Grand total reservation: ~$39.12 (with 20% margin: ~$46.94)" — the round-1 pass corrected
   `campaign-execution-guide.md` but missed this second copy of the same stale number. Corrected
   to the authoritative $162.00/$194.40 figures with a note explaining the correction. A repo-wide
   grep for `39.12`/`46.94` now shows the only remaining occurrences are inside explicit
   "Correction" / round-1-follow-up prose describing what was wrong and why — not live claims.
2. **Bundle test hardening was incomplete**: round 1 hardened `shutil.rmtree` (the cleanup path)
   against Windows `PermissionError`, but the second review reported failures during *directory
   creation* (a `PermissionError` while creating the temporary output directory), a different code
   path the round-1 fix could not have addressed. Added `_mkdir_windows_safe()` to
   `build_release_bundle.py` (retries `bundle_root.mkdir(parents=True, exist_ok=True)` on
   `PermissionError`, the same rationale as the existing rmtree hardening: a path just freed by
   `_rmtree_windows_safe` can still be transiently locked) and applied the same retry to
   `tests/test_eng021_bundle_exclusions.py`'s `setUp()` around `tempfile.mkdtemp()` itself.
   **Important honesty note**: this creation-path failure was, like the round-1 cleanup failure,
   NOT reproduced in this environment — `tests/test_eng021_bundle_exclusions.py` passed 6/6 across
   every run attempted here, both before and after this change. The hardening is a defensible,
   narrowly-scoped mitigation for the exact failure class described, not confirmation that the
   root cause is fixed; a genuinely independent, reproducible run in the reviewing environment
   (or a shared CI job) is still needed before this gap can be called closed with confidence.
3. **Commit-state wording discrepancy**: the round-1 handoff said "nothing has been committed
   yet," which was accurate at the moment that pass's agent finished, but by the time it was
   reported to the reviewer the changes had already been committed (`9f290e8`) and pushed at the
   requesting user's explicit instruction. That was a stale summary sentence, not a repository
   inconsistency — `git log`/`git status` were and remain the source of truth, and this file (and
   `SESSION_HANDOFF.md`) are updated and committed together with the code changes they describe,
   same as every other entry here.

**Still genuinely blocked**, unchanged from round 1: gaps 1 (genuine held-out family), 2
(independent admission reviews), 4 (pilot-derived variance), 5 (project diversity), and 6
(authorization/real campaign) all remain open for the same real human/infrastructure/content
reasons documented above — nothing in this round touched them.

**ENG-021 remains IN_PROGRESS, ENG-022 remains BLOCKED.** No official campaign or release is
claimed or was performed.

## ENG-021 review follow-up, round 3 (2026-09-21)

A third review found round 2's bundle-test hardening still didn't close the gap: the test's
`bundle_root.mkdir(...)` call inside `test_bundle_fails_if_maintainer_slipped_in` bypassed the new
`_mkdir_windows_safe()` entirely (it called `.mkdir()` directly), and — more importantly — the
reviewing environment's failure is a **persistent** `PermissionError` on the OS temp root's parent
directory, not a transient lock. Retrying the same denied path, however many times, cannot help;
round 2's fix addressed the wrong dimension of the problem (retry count) instead of the actual one
(which directory is being written to).

**Fixed:**

1. `test_bundle_fails_if_maintainer_slipped_in` now calls `_mkdir_windows_safe()` like every other
   bundle-directory creation site, instead of a bare `.mkdir(parents=True, exist_ok=True)` — closes
   the "unwrapped path" the review pointed out.
2. Replaced the retry-on-the-same-path approach with `_make_test_tmp_dir()`: the test's scratch
   directory now defaults to `<repo>/.cache/test-tmp` (already gitignored, and necessarily writable
   by anything that can check out and run this test suite) instead of the OS's global temp root,
   with `AIEB_TEST_TMP_ROOT` as an explicit override for a CI/sandbox environment that prefers a
   different location, and the OS default temp root kept only as a last-resort fallback. This is a
   different kind of fix than rounds 1–2: it sidesteps a restricted/denied global TEMP location
   entirely rather than retrying against it, which is the correct response to a *persistent* denial
   (retries only ever help with *transient* locks).

Verified: `tests/test_eng021_bundle_exclusions.py` (6/6) and the full ENG-021 suite (23/23) still
pass here; `.cache/test-tmp` is confirmed empty before and after the run (cleanup still works).
This still cannot be verified as fixed *in the reviewing environment itself* from here — that
requires an actual rerun there, which this pass cannot perform. If `AIEB_TEST_TMP_ROOT`'s default
(`.cache/test-tmp`) is for some reason also denied in that environment, set `AIEB_TEST_TMP_ROOT` to
a location confirmed writable there and rerun.

**Not touched**: gaps 1, 2, 4, 5, 6 remain blocked for the same reasons as rounds 1–2.
**ENG-021 remains IN_PROGRESS, ENG-022 remains BLOCKED.**

## ENG-021 review follow-up, round 4 (2026-09-21)

A fourth review supplied concrete diagnostic evidence from the reviewing host, which changes the
diagnosis: `tempfile.mkdtemp()` itself succeeds there, but every operation INSIDE the directory it
returns — creating a child file or directory, `shutil.rmtree` on it — raises
`PermissionError: [WinError 5]`, even though a plain file written directly under `.cache/` (an
existing, not-freshly-created directory) succeeds. This is conclusive: round 2's relocation
(`.cache/test-tmp` instead of the OS temp root) and round 3's unwrapped-mkdir fix both targeted
*which directory* or *which call site* is used, but the actual restriction is that **this specific
host cannot do nested create/delete inside ANY directory this test process itself just created**,
regardless of where that directory lives. No path chosen from inside this repository can work
around that — `AIEB_TEST_TMP_ROOT` pointed anywhere would hit the identical restriction on the new
directory it creates under that root.

**Fixed, correctly this time**: rather than attempt a fifth guessed relocation, the test now
detects this condition directly. `_tmp_dir_supports_nested_ops()` probes (in `setUp`, before any
real bundle-building work) whether the freshly created temp directory actually supports creating
and removing a child path. If it does not, the test suite reports `SKIPPED` with a precise,
specific reason (quoting the exact `OSError` hit) instead of either a false `PASSED` or a
misleading `FAILED` that looks like a defect in the bundle-building code itself — the review's own
conclusion was that "the bundle implementation itself is not reached far enough to validate its
protected-path logic," i.e. this was never actually exercising the code under test on that host.

Verified here (where nested directory operations work normally): all 6 bundle tests still run and
pass — the probe adds one cheap create/write/rmtree cycle per test and does not change behavior in
a working environment. 23/23 for the full ENG-021 suite.

**What this does and does not close**: it makes the test suite honestly self-report this specific
host limitation as a skip instead of continuing to look like a repo-side bug across four review
rounds. It does NOT itself independently green the six protected-path assertions on the reviewing
host — that verification requires an environment where a process's own freshly created directories
support ordinary child create/delete, which is outside this repository's control to grant.

**Not touched**: gaps 1, 2, 4, 5, 6 remain blocked for the same reasons as rounds 1–3.
**ENG-021 remains IN_PROGRESS, ENG-022 remains BLOCKED.**
