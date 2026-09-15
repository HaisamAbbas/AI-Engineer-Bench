# AI Engineer Bench — Sequential Codex Implementation Prompts

Use with:

- `AI-Engineer-Bench-Architecture-v0.1.md`
- `AI-Engineer-Bench-Implementation-Spec-v1.0.md`

This pack turns the two specifications into bounded implementation phases. The documents describe the target; these prompts sequence the work. They do not replace the specifications, authorize paid campaigns, or certify that anything has been implemented.

## How to use this pack

1. Open Codex in the repository where AI Engineer Bench will be built. Attach both documents. If starting in an empty workspace, Prompt 01 will initialize the project without creating a remote repository.
2. Send Prompt 01, then one numbered prompt at a time. Keep working in the same repository. Do not send all prompts together.
3. Advance when the current phase's prerequisites and acceptance gates pass. If Codex reports an unresolved blocker, use the repair prompt at the end before advancing a dependent phase.
4. In a new chat, send the session-resumption prompt, then the next numbered prompt. Reattach the documents if they are not in the repository.
5. Existing authorization remains valid. Prompts require no repeated permission for ordinary local implementation and tests. Paid provider calls, remote publishing, and cloud provisioning require an existing explicit budget/scope; the prompts do not invent one.

Phases may span multiple Codex turns. They are not estimates of one-turn capacity. A phase is complete based on evidence, not a completion message. A paid run that was not executed remains an open gate even if offline implementation is complete.

## Phase map

| Prompt | Deliverable | Engineering tickets |
| --- | --- | --- |
| 01 | Project bootstrap and delivery ledger | Foundation for all tickets |
| 02 | Harbor/toolchain compatibility spike | ENG-001 |
| 03 | Core contracts, identities and planner | ENG-002 |
| 04 | Safe artifact storage and extraction | ENG-003 |
| 05 | First runnable task and independent evaluator | ENG-004/005 |
| 06 | Execution, deadline and fresh verification | ENG-006/007 |
| 07 | Usage attribution, cost controls and CLI | ENG-008/009 |
| 08 | Two more task families | ENG-010 |
| 09 | Statistics, reports and development pilot | ENG-011/012 |
| 10 | Remaining task suite | ENG-013 |
| 11 | Hosted persistence, API and authentication | ENG-014 |
| 12 | Leased workers and crash reconciliation | ENG-015 |
| 13 | Public results website | ENG-016 |
| 14 | Admin, publication and correction workflows | ENG-017/018 |
| 15 | Official isolation and infrastructure delivery | ENG-019/020 |
| 16 | Official holdout and release preparation | ENG-021/022 |
| 17 | Fixed model-comparison track | ENG-023/024 |
| 18 | Full engineering acceptance review | Cross-cutting release gates |

Prompt 17 is optional for the initial agent-track release. It must not block shipping a correctly scoped agent-track product. Prompt 16 does not permit self-certifying independent human review.

## Prompt 01 — Bootstrap the implementation

```text
You are implementing AI Engineer Bench as a senior software engineer working from these two attached documents:
1. AI-Engineer-Bench-Architecture-v0.1.md
2. AI-Engineer-Bench-Implementation-Spec-v1.0.md

Read both documents fully. The implementation specification refines the architecture and takes precedence where it explicitly resolves an open decision. Current user instructions and existing repository instructions still govern your work.

Inspect the workspace, repository status, and applicable AGENTS.md files. Preserve existing work. If the project does not exist, initialize a local AI Engineer Bench repository in a clear project directory. Do not fork AgentBench or Harbor. Do not create a remote, deploy, publish, or start paid evaluations.

Implement this bootstrap phase:
- Put unchanged copies of the supplied specifications in docs/specs/ if permitted by the workspace and if not already present. If an attachment is inaccessible, ask for that specific file rather than reconstructing it from memory.
- Create the minimal monorepo structure from the specification. Avoid empty placeholder services and unnecessary package boilerplate. Add executable functionality only where it is genuinely needed now.
- Establish the development command entrypoint and documentation. Resolve toolchain versions through verification; defer Harbor pin selection to Prompt 02 if still unknown.
- Create docs/implementation/STATUS.md with all ENG-001 through ENG-024 tickets, dependencies, statuses, acceptance gates, and evidence paths.
- Create docs/implementation/DECISIONS.md for implementation decisions, distinct from the unchanged source specifications.
- Create docs/implementation/SESSION_HANDOFF.md describing current state and the next executable phase.
- Add a concise README describing the product, current implementation state and planned next step. Do not claim benchmark scores or implemented features that do not exist.

Record these continuing working rules in the handoff documentation, without overwriting existing AGENTS.md:
Implement only the requested phase and its necessary prerequisites. Inspect before editing. Tests must exercise behavior. Preserve immutable benchmark evidence. Never invent scores or present mocks as live evaluations. Never weaken hidden-evaluator isolation, scoring, or reproducibility to make a demo pass. Local reversible work should proceed without repeated confirmations. Use already-authorized budgets only. Track blocked gates honestly. Do not automatically commit, push, deploy or publish unless authorized.

Finish with files changed, commands run, what is functional, unresolved decisions, and the recommended next prompt. Do the bootstrap work, not merely a plan for it.
```

## Prompt 02 — Validate the execution foundation

```text
Continue AI Engineer Bench. Read docs/specs/, the implementation ledger, session handoff, and applicable repository instructions. Implement ENG-001: the toolchain and Harbor compatibility spike. Do not build the website or broaden the benchmark scope.

Verify current Harbor source/documentation relevant to the exact version being considered. Keep any exploratory checkout separate from our code. Choose and pin compatible Python/Harbor/package versions based on actual tests. Record the selected commit/release and decision evidence.

Build a minimal disposable integration fixture, separate from the scientific benchmark tasks, that proves:
1. A supported installed coding agent can work in an editable repository with an application service.
2. The execution can be stopped at a deadline without leaving writable contestant processes running.
3. Allowed newly created files can be collected.
4. Collected files can be replayed into a fresh environment and checked externally.
5. The selected backend supports the required resource, network and teardown controls, or explicitly identifies unsupported capabilities.

Keep Harbor integration behind a small adapter boundary. Do not assume lifecycle hooks expose all tool calls or complete cost accounting. Record the evidence coverage available for the chosen entrant.

Run deterministic integration checks without paid providers first. If a real installed-agent smoke run is authorized and credentials exist, run it within the existing cap. Otherwise finish the runnable integration, provide the exact smoke command and mark the real-agent acceptance gate blocked. A fake agent is valid for contract testing, not proof of installed-agent compatibility.

Produce a compatibility report listing pass/fail/not-run for each requirement, pinned versions, limitations and tested commands. Update STATUS.md and SESSION_HANDOFF.md. Stop after this phase; do not silently replace the chosen foundation because one feature needs an adapter.
```

## Prompt 03 — Implement contracts, identities and planning

```text
Continue from the actual repository state and read the two specifications plus handoff. Implement ENG-002 and the core planning primitives needed by later CLI/API work.

Implement strict versioned models for TaskRevision, EntrantRevision, ProtocolRevision, BudgetProfile, Cohort, CampaignDraft, ResolvedCampaign, Trial, Attempt, CandidateManifest, EvaluationPlan, EvaluationResult, EventEnvelope and PublicationManifest. Use the implementation specification's semantics, including nullable unknown usage, separate execution validity and verdict, and distinct engineer/application model profiles.

Implement canonical serialization and content hashes with golden test vectors. Reject unknown schema majors, invalid numeric values, duplicate requirement IDs, unresolved official image references and invalid submission paths. Generate JSON Schemas. Keep core independent of Harbor, web and API imports.

Implement campaign reference resolution, exact trial-matrix expansion, immutable freeze, cohort compatibility checks, deterministic ordering, and validation of repetitions/budgets. A pilot of 3 tasks x 2 entrants x 3 repetitions must resolve to exactly 18 planned trials. Draft values and frozen resolved values must be distinct types/states.

Tests must cover semantic changes affecting hashes, formatting changes not affecting hashes, incomplete profiles, incompatible comparisons, duplicated trial identity, valid and invalid result envelopes, and unknown usage vs zero.

Add realistic schema examples labeled as examples, not benchmark results. Update the ledger and handoff with evidence. Stop when core contracts and planner tests pass; do not implement hosted services yet.
```

## Prompt 04 — Implement safe candidate artifacts

```text
Read the specifications and current code. Implement ENG-003: local artifact storage, immutable manifests, safe collection and extraction. Use the existing core contracts rather than defining competing schemas.

Implement a filesystem-backed content-addressed store with verified reads/writes and a narrow storage interface that can later support object storage. Candidate collection must preserve allowed new/untracked, modified and deleted files relative to the frozen source tree; do not rely solely on git diff.

Validate protected paths, regular file types, normalized relative paths, symlinks/hardlinks, file counts and byte limits. Reject escaping paths, device/socket entries, oversized expansion, and protected-path modification. Never silently omit invalid files and call the submission successful.

Implement deterministic candidate manifests and clean reconstruction against the original task revision. Authoritative hashing/collection must not execute candidate code. Access permissions belong to artifact references; deduplicated content must not leak private artifacts.

Test valid additions/deletions, Unicode paths, traversal, link escapes, protected edits, malformed archives, size limits, missing blobs, corrupted digests and interrupted writes. Keep cleanup safe for referenced artifacts.

Document artifact layout and invariants. Update implementation status with commands and observed results. Do not build a hosted bucket integration before the local contract works.
```

## Prompt 05 — Build the first genuine benchmark task

```text
Read implementation specification Sections 12 and 22–27, especially the fully specified RAG-01 task. Implement ENG-004 and ENG-005 end to end.

Create a small runnable knowledge-service application with the documented stale-version/deletion defect. Implement the public application API, visible development tests/data, task manifest, provenance and version ordering rules. Make the defect realistic and reproducible; do not hide extra requirements.

Create a trusted external evaluator that interacts with the candidate through the API and records authoritative outcomes. It must not import candidate modules into the scorer process. Map every hidden check to a public requirement ID. Keep official answer keys and reference fixes out of the contestant build context.

Implement the reference repair and a genuinely different valid repair where feasible. Add targeted failing candidates: no-op, hardcoded output, stale resurrection after deletion, global rebuild despite incremental constraints, and incorrect citation/version mapping. Counterexamples must exercise plausible shortcuts, not merely mirror evaluator implementation.

Create held-out fixture generation covering creation, updates, repeated events, lower versions, deletion, stale reinsertions and higher-version recreation. Include unaffected documents and external backend write accounting for the published incremental-update constraint.

Run baseline/reference/alternative/counterexample evaluation on fresh environments. Execute the ten-reset reference validation from the specification. Report exact requirements passed/failed and any evaluator instability. Do not replace live application execution with assertions against handcrafted success JSON.

Produce the task package and admission report. Mark independent human review pending if not obtained. Update handoff. Stop before making the remaining suite.
```

## Prompt 06 — Connect execution to fresh verification

```text
Implement ENG-006 and ENG-007 using the existing contracts, artifact layer, Harbor compatibility work and RAG-01 task. Read the current ledger and resolve prerequisites rather than assuming previous phases passed.

Complete the execution adapter and attempt lifecycle: provision, engineer, stop, collect, build, verify, finalize and cleanup. Use the artifact-at-deadline rule. Stop all contestant processes before freezing source; never allow verification time to become extra editing time.

Replay only allowed submitted artifacts into the frozen base in a fresh build allocation. Do not carry over development databases, volumes, hidden state or processes. Execute candidate code in an untrusted allocation and evaluate externally.

Implement explicit attribution for candidate build/runtime failure, configuration failure, resource limits, provider outage, host failure and scorer error. An engineering failure is not automatically retried. Infrastructure replacement attempts retain prior evidence and follow the declared limit.

Test interruption, partial artifact submission, no artifact, deadline during edits, candidate hang, scorer crash, failed teardown, protected-path violation, and a valid fix that depends only on submitted files. Record unsupported official isolation requirements as blocked; do not hide them behind container terminology.

Run a real RAG-01 agent trial if already authorized and possible; otherwise validate the full deterministic lifecycle and leave real-agent validation explicitly pending with exact commands. Update status and stop after the vertical execution path works.
```

## Prompt 07 — Add accounting and the developer CLI

```text
Implement ENG-008 and ENG-009. Read specifications for budget enforcement, CLI behavior and local state. Build on the existing vertical path; do not introduce a hosted dependency for local use.

Implement separate usage attribution for engineer, development application, verifier application and optional judge. De-duplicate receipts without double-counting broker and adapter totals. Preserve unknown costs, billing uncertainty after disconnects and separate physical retries. Hard-cost profiles require conservative in-flight reservations; if a provider cannot support them, label the profile estimated/time-limited instead of claiming enforcement.

Implement aieb doctor, task validate, task verify, plan, run, resume, inspect and report for currently supported scope. Use versioned JSON/JSONL outputs and a readable static HTML report. Follow the specified exit codes: a completed benchmark with failed tasks is not a CLI crash. Include --json and --no-color.

Support local controller locking, frozen-manifest checking on resume, safe cleanup and exact commands in README. Never put credentials in task manifests, CLI output or artifacts. Use existing authorized credentials/budgets only.

Acceptance: a clean local setup can validate RAG-01, plan a trial, execute/replay it, inspect the score and generate a report. Test duplicate usage, lost response, no successes, unavailable usage, interrupted local controller and invalid resume manifest. Real agent success is not a requirement; valid evaluation is.

Update the ledger with implemented vs runtime-verified capabilities. Stop before adding the web application.
```

## Prompt 08 — Add extraction and tool-application tasks

```text
Implement ENG-010: EXT-02 batch/document alignment and TOOL-01 false completion. Read their ticket contracts and the existing RAG-01 admission structure. Reuse generic evaluator utilities, not domain-specific assumptions.

EXT-02 must run a real extraction application with shuffled/batched outputs, partial failures and the declared repeated-ID policy. Verify each result maps to the correct document and valid items are not lost.

TOOL-01 must run a real tool-using application against an externally controlled operation service. Verify that failed operations are not reported as complete and that genuine successful requests still work. The external service ledger, not the candidate's log, is authoritative.

For each task supply public requirements, runnable baseline, visible tests/data, reference fix, valid alternative where feasible, hidden fixtures, shortcut candidates, resource/provenance metadata and admission evidence. Ensure private labels/reference fixes are absent from contestant images.

Execute clean resets and tests that prove each broken baseline fails and real repairs pass. Do not label fixture integration correctness as free-form model quality. Run through the same CLI/execution/replay/report path used by RAG-01.

Update STATUS.md and handoff. The deliverable is three end-to-end task families, not a collection of manifests with unimplemented verifiers.
```

## Prompt 09 — Statistics and the first development pilot

```text
Implement ENG-011 and prepare/execute ENG-012 subject to existing authorization. Read the statistics, eligibility and budget sections fully. Do not build leaderboard metrics from intuition.

Implement per-task s/n, frozen-weight suite/category rates, all-k repeatability, optional pass^k with the correct estimator, Wilson per-task intervals and the specified paired/group-aware analysis. Preserve project-family dependence and show limitations for small samples. Missing planned trials block canonical complete rankings.

Implement cost per resolution with unknown/zero-success handling, successful engineering-time summaries, deadline rates, infrastructure attrition and separately attributed application/verifier costs. Keep application evaluation repetitions distinct from engineering repetitions. Front-end/report consumers must use these outputs, not reimplement statistics.

Create the frozen 18-trial development pilot: three admitted tasks, two validated entrant revisions, three repetitions, one declared profile. Resolve versions, limits, ordering and cost reservation. No agent or provider names/settings may remain unresolved.

Run the pilot only if an explicit existing authorization covers its provider/cloud expenditure. If not, finish the executable plan and offline validation, report the concrete requested cap/credentials or other blocker, and leave ENG-012 not run. Do not invent a budget or substitute scripted results for real entrants.

If executed, retain all attempts, valid failures and costs. Produce an honest pilot report: what broke in the benchmark itself, actual task outcomes, resource consumption, uncertainty and next actions. Do not publish remotely. Update the ledger and handoff.
```

## Prompt 10 — Expand to the twelve-task development suite

```text
Implement ENG-013 after reviewing the pilot and resolving task/evaluator defects that would contaminate expansion. If pilot execution is blocked, explain which authoring work can proceed while keeping that gate open.

Add the remaining nine tasks from the implementation specification. Use at least six underlying application projects across the full twelve-task suite. Do not manufacture apparent diversity by renaming the same repository and counting it as independent evidence.

For every task implement the application baseline, ticket, public contract, development fixtures, reference, evaluator, held-out examples, relevant regression checks, shortcut candidates and admission report. Include family IDs, licenses, resource profiles, evaluator hashes and limitations.

Work task by task or in small batches. Persist progress when a phase spans multiple turns. Do not mark the entire phase complete because directories and TODO files exist. Reference fixes must demonstrably pass and the intended baseline/shortcut failures must demonstrably fail in clean execution.

Create a frozen development suite release manifest only for admitted revisions. Independent review remains pending until actual review occurs; generated self-review cannot satisfy that condition.

Update docs, the suite catalog, CI task gates and the delivery ledger. Report completed, blocked and excluded tasks separately. Stop before official holdout construction or web work.
```

## Prompt 11 — Hosted metadata, API and authentication

```text
Implement ENG-014 from the current specification and core contracts. Preserve local CLI functionality. Read API, database, permission and migration sections before coding.

Implement PostgreSQL models/migrations for task/evaluator/fixture revisions, suites, entrants, campaigns, trials/attempts, candidates, evaluations, artifacts/references, usage, reviews, publications and audit records. Enforce uniqueness, foreign keys, nullable accounting and immutable frozen revisions in persistence, not only in UI checks.

Implement FastAPI endpoints needed for registry/results reads and campaign drafts/freeze. Use OIDC-based maintainer identity with server-side roles. A test identity provider belongs only in isolated tests; production must fail closed when auth is unconfigured. Keep artifact authorization reference-scoped.

Implement typed errors, cursor pagination, optimistic concurrency, idempotency keys and public/private response projections. Generate OpenAPI and typed client artifacts. Public results must come from publication snapshots, not live mutable trial tables.

Test permissions, inaccessible-resource behavior, stale edits, invalid state transitions, idempotent requests, conflicting key reuse, corrupt manifests and migration compatibility on a real test PostgreSQL instance. Use fixture result data only in tests, clearly isolated from actual publications.

Document service startup and environment variables without secrets. Update handoff with implemented endpoints and remaining worker/publication dependencies. Do not deploy remotely.
```

## Prompt 12 — Hosted workers and crash recovery

```text
Implement ENG-015: PostgreSQL-leased execution and verification work. Reuse the proven local execution pipeline and contracts; avoid separate hosted scoring logic.

Implement atomic work acquisition, generation/fencing, heartbeat/expiry, resource allocation, cancellation, artifact-first finalization and reconciliation. Long execution must occur outside database transactions. Prevent stale workers from finalizing after lease reassignment. Exactly-once physical execution is not assumed; enforce one authoritative finalization.

Reconcile uploaded artifacts and active allocations before replacing expired attempts. Retain all attempts/costs. Cleanup must not delete referenced evidence. Maximum invalid replacements follows the frozen campaign policy. Incomplete/cancelled campaigns must not appear complete.

Test two workers contending for work, death before launch, death during engineering, death after upload but before finalization, lease expiry with an old worker returning, verifier outage, duplicate completion, cancellation and orphan teardown. Use controlled failure tests rather than relying on happy-path smoke tests.

Provide local multi-worker launch commands, observability metrics and a reconciliation runbook. Update the delivery ledger with evidence. Do not add Redis or a distributed workflow platform unless a measured constraint requires an ADR.
```

## Prompt 13 — Build the public product website

```text
Implement ENG-016: the AI Engineer Bench public website. Read the page specifications, product appearance, front-end behavior and comparison eligibility sections. Use the existing API and analysis outputs. Do not build a disconnected mock dashboard.

Implement Results, Compare, entrant profile, task catalog/detail, run evidence, Methodology, Releases, Corrections and Run locally/documentation pages. Follow the specified navigation and URL-backed cohort filters. Show suite, track, mode, profile, dates, counts, uncertainty and provenance.

Use React/TypeScript and generated API types. Prefer readable tables, clear typography, tabular numbers and restrained styling. Provide keyboard access, visible focus, semantic tables and text equivalents for charts. Escape all candidate text/diffs/logs. Up to four entrants in side-by-side comparison.

Implement real loading, empty, error, unavailable-cost, incomplete-coverage, redacted-evidence, superseded and withdrawn states. With no actual publication, show an honest preview/empty state. Illustrative fixtures may be used in Storybook/tests only and must never masquerade as live benchmark scores.

Comparison must reject incompatible cohorts for paired statistics. Do not recompute scores in JavaScript. Public evidence must never contain private held-out material.

Test the complete read-only user journey with seeded test data and API integration. Include responsive and accessibility checks, permalink/filter behavior, null sorting, error recovery and malicious log rendering. If visual browser tools are available, inspect screenshots and fix layout issues.

Update README and status. Deliver a locally runnable product, not a hosted deployment or announcement.
```

## Prompt 14 — Admin, publication and corrections

```text
Implement ENG-017 and ENG-018. Read role rules, campaign state semantics, publication provenance and correction workflows. Build on the real hosted workers/API and public website.

Implement campaign draft editing, exact matrix preview, budget reservation, freeze/start/pause/resume/cancel, progress monitoring and invalidity review. UI actions must reflect authoritative server state and show the consequences of cancellation. Never allow editing a frozen plan.

Implement publication preparation from selected immutable evaluations: validate complete cohort, run pinned analysis, produce redacted artifacts, require review, sign manifest and atomically expose public snapshot. Official reviewer cannot approve their own campaign. Single-maintainer review has its own honest label and is not equivalent to independent review.

Implement append-only correction, withdrawal and superseding snapshots. Scorer corrections regrade all affected retained candidates where possible; changing live dependencies requires a new campaign. Preserve old results with notices. Do not mutate historical raw envelopes.

Test role enforcement, self-approval rejection, missing evidence, incomplete cohorts, redaction leaks, duplicate publish calls, signature verification, correction visibility and artifact access. Test a full staging publication using clearly designated fixture data in a nonproduction environment only.

Actual public publication is outside this prompt unless separately authorized. Complete the reviewable local/staging implementation, update the ledger and report open human review gates.
```

## Prompt 15 — Official isolation, CI/CD and operations

```text
Implement ENG-019 and ENG-020 to the extent supported by available infrastructure. Read the security, deployment, CI, retention, observability, release and runbook sections. Do not call a local container setup equivalent to hardened official execution.

Implement disposable allocation provisioning behind a provider boundary, scoped credentials, deny-by-default egress, host/metadata/other-trial isolation, candidate/verifier separation and bounded resource teardown. No contestant Docker socket or evaluator answer-key mount. Provider selection must be supported by capability evidence and recorded in an ADR.

Complete CI pipelines: lint/type/unit/schema/API compatibility, task admission controls, sandbox integration, protected capped live smoke and release candidate gates. Pin dependency/actions references, separate untrusted PR execution from secrets and generate build/SBOM artifacts.

Implement staging/production deployment configuration, compatible database migrations, rollback procedure, backup/restore procedure, worker draining, metrics/alerts, orphan reconciliation and spend kill switch. Keep active campaign toolchains pinned across software upgrades.

Test attempted label/host/cross-trial access, deadline/cleanup failures, retention reference safety and representative restore/rollback behavior. Report actual measured results separately from intended targets.

Use existing authorized cloud scope/budget only. If unavailable, produce complete infrastructure code, local tests, exact staging validation steps and explicitly blocked official gates. Do not provision paid infrastructure or deploy public services merely because this prompt describes them.

Update status and runbooks. Preserve official-release blockers rather than weakening the spec.
```

## Prompt 16 — Prepare the official benchmark release

```text
Implement ENG-021 and prepare ENG-022. Read task admission, holdout, statistics, publication and release acceptance sections. This phase requires real review and campaign execution; software alone cannot satisfy those gates.

Create the holdout curation workflow, provenance/overlap review tools, release manifests and family-split validation. Store private official fixtures and references outside the public repository/build context. Do not call private examples on public tasks contamination-free.

Use pilot variance, project diversity, minimum meaningful effect and available budget to propose a concrete official campaign. Freeze versions, profiles, repetitions, ordering, infrastructure-invalid policy and analysis before seeing official results. Provide the expanded trial count and cost reservation requirements.

Audit all prerequisites: task admission evidence, independent reviews, sandbox enforcement, artifact retention, analysis correctness, accounting coverage, cancellation rules and publication redaction. Prepare contributor/operator documentation and the release candidate bundle.

Run an official campaign only when existing explicit authorization covers the concrete plan and infrastructure/provider expenditure. Publish only when explicitly authorized and the actual required reviews are recorded. Do not substitute your own generated review for independent human approval. Without those prerequisites, finish all possible preparation and mark the release ready-for-review or blocked, not officially released.

Retain valid failures, invalid attempts and exclusions. If anything invalidates comparability, stop publication and document the affected cohort. Update the implementation ledger with exact evidence and remaining actions.
```

## Prompt 17 — Add the LLM model comparison track

```text
Implement ENG-023 and prepare/execute ENG-024 within existing authorization. This is a separate model track, not a rewrite of the agent track. Read the reference execution setup and cohort rules.

Implement a minimal pinned coding loop with file listing/reading/search, patching, bounded command execution, output inspection and submission. Freeze system prompt, tool schemas, context handling, retries and stopping rules. No silent model fallback. Execute inside the same task isolation and artifact-replay boundaries.

Integrate provider adapters preserving supported parameters and recording unsupported controls. Keep application models fixed. Record requested and reported engineer model identity, all additional model usage and budget coverage. Unsupported contexts/settings create disclosed profiles instead of falsely matched comparisons.

Test tool validation, malformed calls, context truncation, deadline behavior, provider error attribution, credential protection and complete candidate collection. Use the existing evaluators/analysis and separate cohort IDs.

Prepare a bounded model-track campaign. Run only under an existing explicit spend authorization; otherwise provide executable commands and leave the live campaign gate pending. Display results separately in the product with accurate interpretation: model performance in this fixed setup.

Update documentation and status. Do not combine agent-track and model-track scores into one ranking.
```

## Prompt 18 — Audit and finish the implementation

```text
Act as a skeptical staff engineer reviewing the actual AI Engineer Bench implementation against both source specifications. Read the repository, delivery ledger, decisions and test evidence. Do not assume prior completion claims are correct.

Build a requirements traceability report: specification requirement -> implementation location -> meaningful test/evidence -> verified/pending/blocked/not applicable status. Check every ENG ticket and all P0/P1/P2/P3 release gates. Distinguish missing infrastructure/credentials/human review from missing software.

Investigate particularly:
- Whether the engineer and application model/cost identities remain separate.
- Whether candidate replay really excludes development state.
- Whether hidden labels or reference fixes enter contestant images or public artifacts.
- Whether fresh independent evaluation rejects hardcoded shortcuts and accepts valid alternatives.
- Whether timeout, invalidity, retries, cancellation and missing trials preserve honest statistics.
- Whether artifact access, lease fencing, idempotency and publication corrections hold under failures.
- Whether UI values come from actual immutable results and expose missing data correctly.
- Whether documentation installation/run commands work on a clean environment.

Fix concrete defects you find with targeted regression tests. Do not perform an unrelated redesign or rewrite the specifications to declare broken behavior acceptable. Record any necessary material change as an ADR and comparability decision.

Run relevant unit, integration, evaluator, API and UI tests. Run paid/cloud tests only within existing authorization. Produce a release-readiness report with evidence, residual risks and explicit blockers. Never mark an official release ready if independent review or real campaign gates remain open.

Finish with the exact locally working user journey, commands to reproduce it, what can ship now, and what cannot yet be claimed. Update handoff so another engineer can continue without reconstructing project history.
```

## Session-resumption prompt

```text
Resume AI Engineer Bench in this repository. Read applicable repository instructions, docs/specs/AI-Engineer-Bench-Architecture-v0.1.md, docs/specs/AI-Engineer-Bench-Implementation-Spec-v1.0.md, docs/implementation/STATUS.md, DECISIONS.md and SESSION_HANDOFF.md. Inspect git status and preserve unfinished/user changes.

Do not restart the project or trust a status label without evidence. Identify the last verified phase and incomplete acceptance gates. If a required source document is missing, request that specific document rather than inventing its content.

I will send the next numbered prompt from the prompt pack. Apply it to the existing implementation, completing its necessary prerequisites and avoiding unrelated work. Preserve the existing scope, decisions and prior authorization. No new paid budget or publishing authorization is implied by resuming.
```

## Repair-and-continue prompt

```text
The current AI Engineer Bench phase has not met its acceptance gate. Read the specifications, status, handoff and actual failing evidence. Diagnose and fix the failure before starting a dependent phase.

Implement the smallest correct repair that preserves the scoring, isolation and reproducibility contracts. Add a regression test addressing the actual cause. Do not remove the check, replace a real integration with a mock, loosen a requirement, or classify a candidate failure as infrastructure-invalid merely to pass.

If the remaining blocker is unavailable credentials, infrastructure or independent review, complete all authorized local work and state the exact remaining gate and reproducible command. Do not fabricate execution evidence.

Update STATUS.md and SESSION_HANDOFF.md with the repaired behavior, verification results and the next valid step. Work to completion within the current phase.
```

## Required phase completion report

Every phase should end with this information, briefly:

1. Implemented functionality and changed files.
2. Tests/commands actually run and their results.
3. Acceptance gates satisfied, pending and blocked.
4. Decisions or specification discrepancies recorded.
5. Exact next command or numbered prompt.

“Implemented,” “tested with fixtures,” “tested with a real agent,” “reviewed independently,” and “published” are different states. Keep them distinct throughout the project.
