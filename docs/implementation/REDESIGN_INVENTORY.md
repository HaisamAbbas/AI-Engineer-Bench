# AI Engineer Bench v2.0 redesign inventory

Inventory date: 2026-09-21  
Governing documents: `AI-Engineer-Bench-Redesign-Spec-v2.0.md` and
`AI-Engineer-Bench-Codex-Prompt-Pack-v2.0.md`  
Phase: Prompt 02 — scope refactor complete (inventory retained)

## Scope and safety

This document is the non-destructive inventory required before the v2.0
refactor. The existing v1 implementation, evidence, migrations, task suites,
uncommitted user work, and git history are preserved. No source, task,
holdout, migration, database, Docker volume, or historical evidence was
deleted or reset while producing this inventory.

The v2.0 redesign is operator-controlled Harbor benchmarking. The public site
is read-only. Official campaigns, private holdouts, provider calls, and
publication remain authorization-gated.

## Baseline

- Repository: `D:\AI-Engineer-Bench`
- Branch: `main`
- HEAD at inventory time: `794ccd6`
- Remote: `origin/main` points at the same historical baseline
- Working tree was already dirty; all pre-existing changes were preserved.
- No destructive git operation was used.

Pre-existing working-tree changes and user material that must not be discarded:

- `.gitattributes`
- `docs/implementation/SESSION_HANDOFF.md`
- `packages/aieb-cli/src/aieb_cli/main.py`
- `AI-Engineer-Bench-Redesign-Spec-v2.0.md`
- `AI-Engineer-Bench-Codex-Prompt-Pack-v2.0.md`
- `suites/real/` and its real-task support files
- `.aieb/` and other local run state
- `examples/rag05-real-baseline-campaign.json`
- `examples/rag05-real-reference-campaign.json`
- `scripts/compute_rag05_digests.py`
- `scripts/run_rag05_admission.py`
- `scripts/run_rag05_model_agent.py`
- `scripts/seed_local_interactive.py`
- `scripts/seed_rag05_real.py`
- `scripts/start_local_api.py`
- `tests/fixtures/rag05_model_agent/`
- `tests/maintainer/rag05/`
- `tests/test_rag05_real_task.py`

The untracked real-task material is user work, not an approved v2.0 admission
or official release. It must remain uncommitted/unpublished until separately
reviewed.

## Component inventory

| Component | Classification | v2.0 role and evidence impact |
|---|---|---|
| `packages/aieb-core` | RETAIN / ADAPT | Core contracts, canonical hashes, tracks, campaigns, releases, verdicts, and publication schemas. Audit old fields against the v2.0 task/release/cohort contracts before changing them. |
| `packages/aieb-runner` | RETAIN / ADAPT | Local execution, artifacts, replay, Harbor boundary, worker lifecycle, model loop, and isolation-facing contracts. Keep Harbor behind the AIEB adapter; do not fork Harbor core. |
| `packages/aieb-analysis` | RETAIN / ADAPT | Aggregation, uncertainty, completeness, cost, reliability, and cohort analysis. Preserve historical analysis evidence while mapping to v2.0 component metrics. |
| `packages/aieb-cli` | RETAIN / ADAPT | Maintainer/reproducibility CLI. Preserve useful validate/verify/run/inspect/report behavior; later align commands with `task`, `release`, `campaign`, `approve`, and `publish`. |
| `services/api` | ADAPT / ISOLATE | Existing persistence, read APIs, operator workflows, auth, leasing, publication, and accounting. Public APIs must remain read-only; any operator surface must be private/authenticated. Audit duplicated or obsolete hosted scope before removal. |
| `apps/web` | RETAIN / ADAPT | Read-only results website. It must consume immutable publication snapshots and never launch campaigns, mutate tasks, or recompute official scores. |
| `suites/dev` | RETAIN as development material | Existing 12 development tasks and admission/evaluator evidence. They are not automatically official or held-out. Recheck task diversity and shared-harness depth under v2.0. |
| `suites/real` | RETAIN as uncommitted user work; ADMIT only later | Real-source `rag.corpus-index-drift` work is outside the curated 12-task registry and has local runtime/admission support. Do not silently add it to official catalogs or publish its local results. |
| `tests/` | RETAIN / ADAPT | Contract, admission, lifecycle, API, Harbor, accounting, security, and model-loop tests. Classify fixture-only versus acceptance evidence; do not weaken tests to make the redesign pass. |
| `tests/maintainer` | RETAIN privately / protect | Trusted evaluator and holdout-side code. Never include it in public bundles or expose it to candidates. |
| `examples/` | RETAIN / RELABEL | Campaigns, pilots, release candidates, and model manifests are historical/proposed fixtures unless explicitly authorized. Preserve status labels and prevent accidental official interpretation. |
| `campaigns/` | ADAPT if present/needed | v2.0 operator campaign manifests and frozen trial matrices. Verify whether current campaign data is old hosted scope or reusable operator material. |
| `manifests/` | RETAIN / ADAPT | Existing release/schema material. Map it to v2.0 immutable release and publication manifests; do not delete older manifests. |
| `docs/implementation` | RETAIN / ARCHIVE historical sections | Evidence, status, decisions, and handoff are unique historical records. Add v2.0 inventory/evidence; do not rewrite old claims to look like v2.0 acceptance. |
| `docs/specs` and root v1 specs | REMOVED | v1 architecture/implementation/prompt copies were deleted after v2.0 cleanup approval; root v2 documents are authoritative. |
| `scripts/` | RETAIN / ADAPT | Admission, analysis, bundle, smoke, migration, seed, and operator scripts. Trace imports and authorization behavior before removing any. |
| `deploy/` and `infra/` | ADAPT / retain evidence | CI, deployment, alerts, and environment documentation. Official deployment remains authorization-gated; do not infer production readiness from local files. |
| `.github/` | RETAIN / ADAPT | CI and artifact checks. Audit triggers, permissions, generated-artifact checks, and v2.0 clean-checkout commands. |
| `README.md`, `STATUS.md`, `DECISIONS.md`, `SESSION_HANDOFF.md` | RETAIN / update after inventory | Keep historical status accurate. New v2.0 changes must distinguish implemented, historical, blocked, fixture-only, and official states. |
| `.venv`, `.cache`, `.pytest_cache`, `.ruff_cache`, `pytest-cache-files-*`, `.aieb`, `.claude`, `.commandcode` | GENERATED/LOCAL; inspect before cleanup | These are local state or tooling artifacts. They were not deleted because ownership, active processes, and evidence content were not fully established. Remove only verified disposable contents in a later bounded cleanup. |

## Hermes-like or out-of-scope functionality to isolate

The following must not remain primary AIEB product scope in v2.0:

- interactive evaluation of user-provided private applications;
- arbitrary customer repository workflows;
- public task submission and automatic task generation;
- chat-based benchmark control;
- public campaign start, budget mutation, evaluator mutation, or publication approval;
- generic agent-framework/observability features unrelated to benchmark execution.

No such functionality was deleted during this inventory. Prompt 02 must trace
imports/tests and archive or isolate it with evidence before removal.

## Evidence and private-data handling

- Existing admission, evaluator, publication, security, migration, and Harbor
  evidence is historical and must remain addressable.
- Existing `suites/dev` private-example labels do not create genuine official
  holdouts. v2.0 official holdouts require separate access-controlled fixtures
  and references outside the public repository/build context.
- The real-task work under `suites/real` is not an official release or
  contamination-free holdout.
- No private holdout directory was opened, copied, or moved during inventory.

## Disposable-artifact findings

The checkout contains generated/local paths including `.cache`, `.aieb`,
`.pytest_cache`, `.ruff_cache`, `.venv`, `.commandcode`, and several
`pytest-cache-files-*` directories. They are ignored or local tooling paths,
but some have host permission behavior and may contain useful run evidence.
They were intentionally left untouched. A later cleanup may remove only paths
that are confirmed disposable, inactive, and not evidence or user work.

## First v2.0 refactor boundary

Prompt 02 was completed without destructive scope removal: tracing found no
dedicated obsolete Hermes/private-app implementation to archive. The verified
boundary and retained development-only material are recorded in
`REDESIGN_SCOPE.md`. The Harbor adapter, contracts, task/evaluator concepts,
artifact safety, analysis, CLI, and evidence remain intact.

## Inventory completion report

### Implemented functionality and changed files

- Added this inventory: `docs/implementation/REDESIGN_INVENTORY.md`.
- Added the verified scope boundary: `docs/implementation/REDESIGN_SCOPE.md`.
- Updated the README to distinguish private operator routes from the public
  read-only website and to point at the v2 governing documents.
- No source, task, migration, database, Docker volume, or historical evidence
  was deleted or reset.

### Tests/commands actually run and results

- `git status --short`, `git branch --show-current`, `git log -20 --oneline --decorate`, and `git diff --check` — baseline inspected; pre-existing dirty worktree preserved.
- Repository/package/suite listings and targeted README/status/spec inspection — completed.
- No paid provider, campaign, publication, deployment, or private-holdout operation was run.

### Acceptance gates

- Satisfied: v2.0 governing documents identified; current worktree and history preserved; top-level components classified; out-of-scope boundary documented; private/official claims not upgraded.
- Pending: maintainer review of the retained/adapted classifications and the
  v2 task/admission contracts.
- Blocked: official campaign, provider execution, private holdout construction, publication, and production deployment remain authorization-gated.

### Decisions or specification discrepancies

- The current repository contains substantial v1 hosted/operator implementation
  and uncommitted real-task/model-demo work. It is not safe to reset it into a
  blank v2 tree.
- Existing v1 status labels are historical evidence, not v2.0 completion claims.
- `suites/real` remains outside the curated development catalog pending review.

### Exact next command or numbered prompt

- Review `docs/implementation/REDESIGN_SCOPE.md` alongside this inventory.
- Then run **Prompt 03 — Define the v2 task package and admission contracts**.
