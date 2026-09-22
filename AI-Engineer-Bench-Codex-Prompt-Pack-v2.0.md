# AI Engineer Bench — Codex Refactor and Implementation Prompt Pack

Use this pack with AI-Engineer-Bench-Redesign-Spec-v2.0.md.

Send one prompt at a time from the same repository. The coding agent must inspect before editing and must not silently preserve old scope merely because it already exists.

## Phase map

| Prompt | Deliverable | Definition of done |
|---|---|---|
| 01 | Repository inventory | Every module classified retain/adapt/archive/remove |
| 02 | Safe refactor | Unrelated Hermes/private-app scope isolated or removed with evidence preserved |
| 03 | Benchmark contracts | Track, task, release, campaign, trial, verdict, publication schemas validate |
| 04 | Harbor adapter | One pinned Harbor release and real supported-agent path verified or honestly blocked |
| 05 | Task admission | Baseline/reference/alternative/shortcut controls work |
| 06 | Campaign/reliability runner | Repetition, artifact collection, hidden replay, failure attribution work |
| 07 | Deep MVP-1 suite | Three genuine AI applications and meaningful tasks exist |
| 08 | Analysis/releases | Metrics, uncertainty, incomplete cohorts, immutable release manifests work |
| 09 | Operator workflow | Maintainer-only task, campaign, release, publication commands work |
| 10 | Public website | Read-only charts consume published snapshots |
| 11 | MVP-2 bug finding | Repository snapshots, hidden labels, findings, and scoring work |
| 12 | Final audit | Scope, integrity, reproducibility, and release gates are honestly reported |

Every phase must end with the required completion report at the end of this file.

## Prompt 01 — Inventory before refactoring

    You are refactoring the existing AI-Engineer-Bench repository according to AI-Engineer-Bench-Redesign-Spec-v2.0.md.

    Read the specification completely. Inspect repository status, history, instructions, README, STATUS, packages, services, apps, Harbor integration, task suites, tests, and documentation. Do not edit or delete implementation yet.

    Produce docs/implementation/REDESIGN_INVENTORY.md with one row for every top-level package, service, app, suite family, and major workflow. Classify each RETAIN, ADAPT, ARCHIVE, or REMOVE under the new product boundary. For every archive/remove decision record imports, tests, unique evidence, and a safe plan. Identify exactly what belongs to the separate Hermes-like project and must not remain primary AIEB scope.

    The target product is operator-controlled Harbor benchmarking: hundreds of hard AI-engineering tasks over time, public read-only charts, private campaigns, and MVP-2 public-repository bug finding. Users do not control campaigns from the public site. Do not add Hermes-like interactive application evaluation.

    Do not change source code, delete files, run paid model calls, publish results, or modify hidden data. Finish with the required phase completion report and exact next prompt.

## Prompt 02 — Refactor scope safely

    Implement the approved inventory from Prompt 01. Read the redesign specification, inventory, STATUS, and repository instructions first.

    Refactor around these boundaries:
    - retain/adapt Harbor integration, task contracts, evaluators, local execution, artifact safety, analysis, and evidence;
    - retain only operator campaign/publication capabilities required by the new specification;
    - remove or isolate Hermes-like private application evaluation, arbitrary customer repository workflows, interactive benchmark control, redundant hosted features, and obsolete scope;
    - keep the public website read-only for published results.

    Do not use broad destructive deletion. Before removing a module, search imports/tests and either archive historical material with a clear note or delete only after proving it is unreachable and recording the decision. Preserve historical evidence and git history. Update packages, README, STATUS, docs, and tests. Do not weaken isolation or turn blocked claims into completed claims.

    Run the existing regression suite and targeted tests. Do not run paid evaluations, publish, deploy, or change official/private fixtures. End with the required report.

## Prompt 03 — Implement benchmark contracts

    Implement strict versioned contracts and validators for task revisions, Track A and Track B protocols, evaluators, agent/model configurations, releases, campaigns, trials/attempts, verdicts, and publication snapshots.

    Keep Harbor task.toml compatibility behind an adapter. AIEB manifests must carry real digests, family, difficulty, license/provenance, network policy, resource limits, public/hidden status, evaluator revision, and protocol version. Reject unknown schemas, floating images, unresolved hashes, invalid paths, missing license metadata, and incomplete campaign cells before dispatch.

    Add golden vectors and negative tests. Preserve valid existing contracts but migrate obsolete fields explicitly. Do not implement a universal opaque score. Keep reliability and correctness as separate metrics. Run unit, canonicalization, migration, and regression tests. End with the required report.

## Prompt 04 — Rebuild the Harbor boundary

    Implement or simplify the AIEB Harbor adapter. Harbor remains an execution dependency, not a forked product core.

    Pin a tested Harbor release and record compatibility evidence. Translate an AIEB trial into a Harbor job and normalize trajectory, verifier output, reward metrics, artifacts, timing, provider/model identity, and failures.

    Verify a real supported-agent path where existing authorization, credentials, and caps permit it. Otherwise run deterministic/reference compatibility tests and mark the real gate BLOCKED; do not claim agent compatibility from mocks. Test startup, timeout, deadline collection, new-file collection, hashing, verifier completion, teardown, and normalization.

    Use a separate verifier environment for hidden evaluation. Do not assume the default shared verifier is secure. Do not modify Harbor core to bypass an AIEB boundary. End with the required report.

## Prompt 05 — Task authoring and admission

    Implement maintainer task creation and admission.

    A task must include instruction, starter repository, public fixtures/tests, private evaluator references, reference, baseline, alternative, shortcut/adversarial controls, environment metadata, real content digests, and license/provenance.

    Admission verifies clean checkout execution, intended baseline failure, reference pass, independent alternative pass, shortcut failure, clean resets, no private-fixture leakage, public/hidden requirement consistency, and reproducible digests.

    Keep admission separate from official publication. Require independent review metadata but do not self-certify human review. Do not bulk-create shallow variants. Add commands and tests. Label existing tasks development-only until their gates pass. End with the required report.

## Prompt 06 — Campaign and reliability protocol

    Implement the operator campaign runner around Harbor.

    Freeze a campaign manifest, expand task x entrant x repetition cells, enforce deadlines, collect allowed files including new files, replay candidates in clean evaluation environments, and store immutable trial/attempt evidence.

    Support PASS, FAIL, CONTRACT_VIOLATION, INDETERMINATE, and INFRASTRUCTURE_INVALID. Candidate and infrastructure failures must remain distinct. Replacement attempts retain all records and cannot improve a scored result. Incomplete cohorts are ineligible for canonical rankings.

    Track A reports target behavior, hidden behavior, regression preservation, fault/recovery checks, cost, latency, steps, and tool-use metrics separately. Test deadlines, stale workers, clean replay, hidden isolation, traversal rejection, unknown usage, zero successes, missing cells, and repeated runs. Do not run paid campaigns. End with the required report.

## Prompt 07 — Deep MVP-1 task suite

    Replace shallow or duplicate variants with a defensible MVP-1 suite built from three genuinely different AI applications:
    1. RAG with indexing, retrieval, metadata, citations, and freshness;
    2. structured extraction/data processing with schemas, batching, malformed inputs, and partial failure;
    3. tool workflow with state, confirmation, idempotency, ambiguity, and recovery.

    Create a meaningful initial task set. Each task requires a real ticket, visible contract, hidden cases, regression requirements, operational edge case, reference, baseline, alternative, and shortcut control. Do not merely rename copies of a shared tiny HTTP harness.

    Use synthetic or redistributable data. Record family IDs and provenance. Separate public development material from private official holdouts. Run admission matrices and resets where required, but label results development until independent review and official gates pass.

    Do not add web or hosted features in this phase. End with the required report and list shallow/blocked tasks.

## Prompt 08 — Analysis and release artifacts

    Implement analysis and immutable release artifacts.

    Aggregate per-task, family, capability, difficulty, and entrant results. Report pass@1, pass^k, repeated-run uncertainty, regression preservation, recovery, cost, latency, steps, tool efficiency, infrastructure-invalid rate, and completeness. Do not rank incomplete/incompatible cohorts or fabricate intervals.

    Release manifests freeze tasks/evaluators, Harbor/environment versions, agent/model protocol, limits, repetitions, scoring, exclusions, and hashes. Publication snapshots are immutable and corrections/supersessions preserve history.

    Test zero successes, all-invalid attempts, missing cells, replacements, grouped analysis, mixed cohorts, unknown costs, and corrected verdicts. Generate a local static report from a real fixture campaign. End with the required report.

## Prompt 09 — Operator-only workflow

    Implement or simplify maintainer commands equivalent to:
    aieb task validate
    aieb task admit
    aieb release prepare
    aieb campaign plan
    aieb campaign run
    aieb campaign inspect
    aieb campaign approve
    aieb publish

    The public API must not expose campaign start, budget control, task/evaluator mutation, or publication approval. If an operator API remains, isolate it with private authentication and roles. Preserve idempotency, immutable revisions, approvals, correction, supersession, and audit evidence.

    Do not add conversational UX; Hermes belongs elsewhere. Operator output should be scriptable JSON plus concise summaries. Run command, authorization, idempotency, publication, and fixture-journey tests. Do not claim official publication. End with the required report.

## Prompt 10 — Public results website

    Refocus the website as a read-only benchmark explorer.

    Implement release overview, agent/model rankings, capability/difficulty breakdowns, reliability/recovery charts, task details, evidence summaries, methodology, limitations, release history, and corrections. Consume published snapshots or read-only API responses; never recompute official metrics client-side.

    Show release, cohort eligibility, valid count, uncertainty, configuration, cost assumptions, limitations, and development/self-reported/official status. Incompatible comparisons must be blocked or labeled.

    Remove public controls for launching campaigns, uploading tasks, changing evaluators, or publishing. Run API contract, accessibility, empty/error state, snapshot-integrity, and browser tests with fixture data only. End with the required report.

## Prompt 11 — MVP-2 public-repository bug finding

    Implement MVP-2 as a separate track for bug finding in fixed public AI repositories.

    Define repository snapshot, documentation, allowed commands, objective, finding schema, public examples, hidden labels, severity taxonomy, and optional patch mode. Keep finding-only and patch tracks separate.

    A finding requires location, reproducible steps, observed/expected behavior, impact/severity, evidence, and confidence. Score true positives, false positives, duplicates, severity accuracy, reproduction quality, and patch correctness where applicable. Do not reward speculative lists.

    Use compatible repository licenses. Create baseline, reference, alternative, and negative/no-bug controls. Protect hidden labels. Use a separate release/cohort ID so MVP-2 is never blended silently with Track A. End with the required report.

## Prompt 12 — Final skeptical audit

    Audit the repository against AI-Engineer-Bench-Redesign-Spec-v2.0.md.

    Trace every requirement to implementation, test, and evidence. Check that Hermes/private-app functionality is not AIEB scope; Harbor stays behind an adapter; the website is read-only; operator controls are private; holdouts are protected; no fake scores or official claims exist; incomplete cohorts cannot rank; infrastructure failures are attributed; task diversity is genuine; and publication artifacts are immutable.

    Fix concrete in-scope defects. Do not weaken tests, delete evidence, relabel development results, or rewrite the specification to pass. Separate implemented, fixture-only, authorization-blocked, content/review-blocked, and published states.

    Produce docs/implementation/FINAL_AUDIT.md with requirement/evidence matrix, blockers/owners, clean-checkout commands, Harbor/agent versions, task/release inventory, and what has not been measured. End with the required report.

## Required phase completion report

Every prompt must end with:

    Phase completion report

    Implemented functionality and changed files:
    - ...

    Tests/commands actually run and results:
    - command: ...
    - result: ...

    Acceptance gates:
    - satisfied: ...
    - pending: ...
    - blocked: ...

    Decisions or specification discrepancies:
    - ...

    Exact next command or numbered prompt:
    - ...

## Repair-and-continue prompt

    A previous phase has a concrete failure. Read its report, failing output, relevant source, and redesign specification. Reproduce first. Fix the smallest root cause without weakening the gate, replacing real execution with a mock, exposing hidden data, deleting evidence, or relabeling blocked results as complete. Add a regression test, run focused and relevant regression suites, update STATUS/evidence, and end with the required completion report.

## Session-resumption prompt

    Resume from the repository’s current state. Read AI-Engineer-Bench-Redesign-Spec-v2.0.md, STATUS.md, REDESIGN_INVENTORY.md, SESSION_HANDOFF.md, the latest phase report, and repository instructions. Inspect git status and preserve work. Do not repeat completed phases. Identify the first unmet gate and continue with the next numbered prompt. Do not publish, deploy, run paid campaigns, or access private holdouts without explicit authorization. End with the required completion report.

