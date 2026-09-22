# AI Engineer Bench — Harbor-Based Benchmark Product Specification

Version: 2.0  
Status: implementation baseline  
Date: 21 September 2026

## 1. Executive decision

AI Engineer Bench is a centrally managed public benchmark measuring how well LLM-powered coding agents perform practical AI-engineering work.

Harbor is the execution substrate. AI Engineer Bench owns task curation, hidden evaluation policy, result normalization, benchmark releases, analysis, and the public website.

The website is read-only for users. Maintainers control task creation, campaign execution, model configuration, budgets, releases, and publication through private operator commands and manifests.

The separate Hermes-like project evaluates user-provided AI applications interactively. It is not part of AI Engineer Bench and must not be reimplemented here.

## 2. Product promise

AI Engineer Bench measures whether LLM agents can perform difficult, realistic AI-engineering tasks—not merely write code or answer questions.

The benchmark evaluates agents on real application repositories, AI system behavior, integration boundaries, retrieval, structured data, tool use, evaluation code, model configuration, and failure handling.

It reports performance on a versioned task suite under a disclosed execution protocol. It does not claim to measure the complete ability of an AI engineer.

## 3. Product boundaries

### Included

- Hundreds of versioned AI-engineering tasks over time.
- Harbor-compatible task environments.
- Coding-agent and model comparisons.
- Public development tasks and private official holdouts.
- Deterministic and carefully controlled semantic evaluators.
- Repeated trials and uncertainty reporting.
- Difficulty and capability breakdowns.
- Cost, latency, steps, tool-use, and reliability diagnostics.
- A read-only public results website.
- Maintainer-only task, campaign, and publication workflows.
- A local CLI for maintainers and reproducibility.

### Excluded

- Hermes-like interactive application evaluation.
- User-uploaded private application evaluation.
- Public task submission in the first release.
- Generic observability or agent-framework functionality.
- Chat-based evaluation control.
- A universal score without a defined cohort.
- Uncontrolled internet access during official trials.
- Automatic task generation without human review.
- Deep modification of Harbor core.

## 4. Evaluation tracks

### Track A — AI Application Engineering

An agent receives a runnable AI application repository and an engineering ticket. It must diagnose, modify, test, and submit a working repository.

Examples include repairing stale RAG indexes, preserving citations while changing retrieval, fixing extraction under malformed inputs, preventing duplicate tool side effects, adding recovery after tool failure, correcting model integration, and repairing evaluation code.

### Track B — AI Application Bug Finding (MVP 2)

An agent receives a fixed public repository, documentation, tests, and a bug-finding objective. It must submit structured, reproducible findings or a patch, according to the task protocol.

Measure true-positive discovery, severity classification, reproduction quality, root-cause accuracy, patch correctness where applicable, false-positive rate, and regression safety. Speculative issue lists must not score.

### Track C — Fixed model comparison

Freeze one reference agent, tool policy, context policy, and task protocol. Vary the model only. Keep this cohort separate from Track A.

## 5. Task package

Every task is an immutable revision:

    task/
    ├── manifest
    ├── instruction
    ├── starter repository
    ├── environment and dependency lock
    ├── public fixtures/tests
    ├── private holdout fixtures/tests
    ├── reference solution
    ├── baseline solution
    ├── alternative solution
    ├── shortcut/adversarial solutions
    └── evaluator revision

The manifest declares task ID/revision, family, tags, difficulty, license, source commit, content digests, Harbor environment, time/resource limits, network policy, submission paths, protected paths, public contract, evaluator ID, and development/official status.

Unknown fields, floating image tags, unresolved hashes, missing licenses, and invalid submission policies fail validation before dispatch.

## 6. Initial task families

- RAG and retrieval: freshness, chunking, filters, citations, embedding migration, reranking, isolation, partial index failure.
- Structured extraction: schema evolution, missing/malformed fields, batch alignment, units, partial failures, idempotency, abstention.
- Tool workflows: false completion, ambiguous acknowledgment, duplicate side effects, stale arguments, session isolation, authorization, compensation, recovery.
- AI evaluation/model integration: evaluator bugs, structured outputs, provider migration, prompt/config regression, judge calibration, rate limits.
- Public-repository bug finding: retrieval bugs, control-flow bugs, tool-contract violations, leakage, evaluator errors, swallowed exceptions, state/concurrency defects.

Task growth must come from genuinely different applications and engineering mechanisms, not renamed variants of a tiny shared harness.

## 7. Difficulty and task quality

A strong task contains an existing application, clear user-visible requirement, non-obvious root cause, useful but incomplete visible tests, hidden combinations, regression constraints, an operational edge case, multiple valid implementations, and shortcut controls.

Hidden tests may hide examples and combinations, but may not introduce undisclosed requirements.

## 8. Harbor boundary

Harbor launches the configured agent, provisions the task environment, captures trajectories and artifacts, runs the verifier, and records raw rewards/logs. Harbor tasks use task.toml, instruction.md, an environment, and verifier tests; verifiers write numeric rewards to the verifier reward JSON or text file.

AIEB owns the thin adapter and normalizes Harbor output into trial records.

Harbor owns agent adapters, sandbox lifecycle, task execution, trajectory capture, verifier invocation, raw logs/artifacts, and provider/concurrency execution.

AIEB owns task admission and review, public/private split, campaign manifests, cohort comparability, trial validity, infrastructure attribution, aggregation, uncertainty, release versioning, publication, and corrections.

Pin a tested Harbor release. Do not fork Harbor core unless separately approved.

For hidden evaluation use a separate verifier environment when the agent must not see grading code or holdout data. Harbor’s default shared verifier mode is not sufficient for such tasks.

## 9. Evaluation protocol

For Track A:

1. Freeze the campaign manifest.
2. Provision a Harbor task environment.
3. Give the agent instruction, repository, public fixtures, and declared tools.
4. Record trajectory, commands, tool calls, timing, and usage where available.
5. Stop at submission or deadline.
6. Collect allowed files, including new files.
7. Destroy the engineering environment.
8. Build the candidate in a clean evaluation environment.
9. Run public-regression and private-holdout evaluators outside agent access.
10. Record per-requirement verdicts, metrics, evidence, and hashes.
11. Classify the trial as scored, candidate failure, or infrastructure-invalid.

For Track B freeze repository snapshot, known-bug inventory, finding schema, public examples, and hidden labels. Require location, reproduction, observed/expected behavior, impact, evidence, and confidence.

## 10. Evaluation layers

Each Track A task declares applicable layers:

1. Build and startup.
2. API or artifact contract.
3. Requested behavior.
4. Hidden generalization.
5. Regression preservation.
6. Operational/fault behavior.
7. Resource and safety constraints.

Mandatory predicates define primary success. Diagnostic metrics remain separate.

## 11. Scoring and reporting

Publish component metrics rather than an opaque composite:

- task success;
- hidden-case success;
- regression preservation;
- recovery;
- bug-finding precision/recall for MVP 2;
- pass@1 and pass^k;
- repeated-run variance;
- cost and latency;
- steps and tool efficiency;
- infrastructure-invalid rate;
- capability/difficulty coverage.

An overall index is optional and only allowed if formula, cohort, weighting, missingness, and uncertainty are public. Do not rank incomplete cohorts or treat infrastructure failures as candidate failures.

## 12. Releases

A release freezes tasks, fixtures, evaluators, Harbor/environment versions, agent/model protocol, limits, repetitions, scoring, exclusions, and publication schema.

Stages:

- dev-0.x: local tasks and development reports;
- preview-0.x: reviewed public suite, no official leaderboard claim;
- official-1.0: real holdouts, independent review, authorized campaign, complete cohort;
- later releases: additive tasks or separate cohorts;
- major releases: changes invalidating direct comparison.

Old publications remain addressable. Corrections create superseding snapshots.

## 13. Operator workflow

    aieb task init
    aieb task validate suites/dev/rag-001
    aieb task admit suites/dev/rag-001
    aieb release prepare --suite aieb-core --version 0.1
    aieb campaign plan --release aieb-core@0.1 --agents campaign/agents.yaml
    aieb campaign run --plan campaigns/aieb-core-0.1.json
    aieb campaign inspect --campaign aieb-core-0.1
    aieb campaign approve --campaign aieb-core-0.1
    aieb publish --campaign aieb-core-0.1

The public website has no campaign-control endpoints. Any operator API is private, authenticated, and separated from read-only public APIs.

## 14. Public website

Pages include latest release, agent/model ranking, capability breakdown, difficulty breakdown, reliability/recovery charts, task detail, evidence summary, methodology, release history, limitations, and corrections.

The frontend never recomputes official scores. It reads signed or hashed publication snapshots.

Every result view shows release version, cohort eligibility, valid trial count, uncertainty, configuration, cost assumptions, limitations, and whether results are development, self-reported, or official.

## 15. Repository redesign

The coding agent must inventory the existing repository before editing. Preserve useful Harbor integration, contracts, CLI execution, artifact safety, task/evaluator concepts, analysis, and evidence. Adapt, archive, or remove unrelated Hermes/private-app workflows, speculative user controls, redundant platform features, and obsolete scope.

Removal rules:

- classify every module retain/adapt/archive/remove;
- search imports and tests;
- archive historical material when useful;
- do not delete unique evidence before recording its location;
- run regression tests after bounded removals;
- update status and documentation to actual state.

Target center:

    packages/aieb-core       contracts, releases, planning, scoring
    packages/aieb-harbor     thin Harbor adapter
    packages/aieb-runner     operator campaign execution
    packages/aieb-analysis   aggregation and uncertainty
    packages/aieb-cli        maintainer commands
    suites/                  versioned tasks and public fixtures
    campaigns/               operator manifests
    services/api             read-only publication API; private operator API isolated
    apps/web                 public charts and evidence
    docs/                    methodology and task-author guide
    tests/                   contracts, admission, integration

## 16. MVP plan

### MVP 1

- Harbor adapter verified with one real supported agent.
- Three deep AI-application projects.
- Twelve to twenty high-quality tasks.
- Public development and private holdout cases.
- Reference, baseline, alternative, and shortcut controls.
- Repeated campaigns and reliability metrics.
- Read-only result website.
- One complete development release.

### MVP 2

- Fixed public repository snapshots.
- Hidden bug labels.
- Finding schema and evidence requirements.
- Precision/recall and severity reporting.
- Separate finding and patch tracks.
- Separate release/cohort identifier.

## 17. Definition of done

The redesign is complete when unrelated Hermes/private-app functionality is no longer presented as AIEB scope; one real Harbor agent runs a task end to end; packages validate from a clean checkout; three deep applications and a meaningful initial suite exist; holdouts are genuinely separate and access-controlled; controls pass; repeated campaigns produce immutable reports; invalid/incomplete trials are explicit; the website shows published snapshots only; operators can add/release tasks without changing the core runner; and no official score is claimed without an authorized complete campaign.

## 18. Integrity rules

Never fabricate scores, agent runs, admissions, or official status. Never expose holdouts, allow candidates to alter evaluators, introduce undisclosed requirements, blend incomparable cohorts, turn infrastructure failures into candidate failures, or delete evidence to make the repository appear complete.

