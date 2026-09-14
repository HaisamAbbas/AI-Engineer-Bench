# Engineering status

Updated: 2026-09-14
Current phase: Prompts 08-09 — ENG-010/011 complete; ENG-012 prepared and blocked on authorization
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
| ENG-003 | Local artifact store and safe extraction | ENG-002 | COMPLETE | EX-01 retains allowed untracked files. SE-01 rejects escaping symlinks. Validate sizes, paths, file types, manifests, and content integrity. | `docs/implementation/evidence/ENG-003/artifact-store.md` and `tests/test_candidate_artifacts.py` |
| ENG-004 | RAG-01 application and public contract | ENG-002 | COMPLETE | Reproducible intentionally broken baseline; public ticket and API contract complete; all hidden checks map to public requirements. | `suites/dev/rag.document-freshness/` and `docs/implementation/evidence/ENG-004-005/admission-report.md` |
| ENG-005 | RAG-01 trusted verifier, reference, and counterexamples | ENG-004 | COMPLETE | EV-01 baseline fails intended checks; EV-02 reference and alternative pass; EV-03 shortcuts fail; ten clean fixture resets succeed. Independent human review remains pending for official admission. | `tests/maintainer/rag01/`, `scripts/run_rag01_admission.py`, and `docs/implementation/evidence/ENG-004-005/admission-report.json` |
| ENG-006 | Executor adapter and deadline protocol | ENG-001, ENG-003 | COMPLETE | EX-02 freezes artifact at deadline after stopping owned engineering processes, records reason/attribution, preserves evidence, and verifies allocation cleanup. Real installed-agent validation and official isolation remain blocked. | `packages/aieb-runner/src/aieb_runner/lifecycle.py`, `tests/test_attempt_lifecycle.py`, and `docs/implementation/evidence/ENG-006-007/vertical-lifecycle.md` |
| ENG-007 | Fresh candidate build and replay | ENG-003, ENG-005, ENG-006 | COMPLETE | Replays allowed submitted artifacts over pristine RAG-01 base in a new build allocation and evaluates live candidate code externally; no engineering allocation, process, or state is reused. Official isolation remains blocked. | `packages/aieb-runner/src/aieb_runner/lifecycle.py`, `tests/test_attempt_lifecycle.py`, and `docs/implementation/evidence/ENG-006-007/vertical-lifecycle.md` |
| ENG-008 | Role-separated usage and caps | ENG-006 | COMPLETE | Separate role ledger deduplicates broker/adapter receipts, preserves unknown/lost billing, and distinguishes physical retries. Hard-cost enforcement is explicitly unavailable without provider reservations. | `packages/aieb-runner/src/aieb_runner/accounting.py`, `tests/test_accounting_and_cli.py`, and `docs/implementation/evidence/ENG-008-009/local-cli.md` |
| ENG-009 | CLI planner, run, inspect, and report | ENG-002, ENG-007, ENG-008 | COMPLETE | Local RAG-01 validate/plan/run/resume/inspect/report path writes frozen JSON/JSONL state and static HTML; normal failed tasks are data, not CLI crashes. | `packages/aieb-cli/`, `tests/test_accounting_and_cli.py`, and `docs/implementation/evidence/ENG-008-009/local-cli.md` |
| ENG-010 | EXT-02 and TOOL-01 vertical tasks | ENG-005, ENG-009 | COMPLETE | Both new families pass baseline/reference/alternative/shortcut/reset admission gates and reference repairs run through validated fresh CLI path. Independent review remains pending. | `suites/dev/ext.batch-alignment/`, `suites/dev/tool.false-completion/`, and `docs/implementation/evidence/ENG-010/admission-report.md` |
| ENG-011 | Analysis and coverage eligibility | ENG-002, ENG-009 | COMPLETE | ST-01 zero-success cost is undefined; ST-02 incomplete plans cannot produce a canonical complete rank; ST-03 preserves project/family clustering; no fabricated intervals. | `packages/aieb-analysis/`, `tests/test_analysis.py`, and `docs/implementation/evidence/ENG-011/analysis.md` |
| ENG-012 | Eighteen-trial development pilot | ENG-010, ENG-011 | BLOCKED | Offline 3-task x 2 deterministic-fixture entrant x 3 repetition matrix is frozen. Real pilot is not run: no explicit provider/cloud authorization, credentials, or approved cap. | `examples/development-pilot-18.json` and `docs/implementation/evidence/ENG-012/pilot-preparation.md` |
| ENG-013 | Remaining nine tasks and independent reviews | ENG-012 | BLOCKED | Twelve tasks across at least six projects pass admission gates and receive required independent review. | `docs/implementation/evidence/ENG-013/` (planned) |
| ENG-014 | API auth, persistence, and migrations | ENG-002 | BLOCKED | API-01 returns 409 for reused idempotency key with changed body; API-02 hides unauthorized private artifact refs; migration compatibility is tested. | `docs/implementation/evidence/ENG-014/` (planned) |
| ENG-015 | PostgreSQL worker leasing and reconciliation | ENG-006, ENG-014 | BLOCKED | EX-03 reconciles a crash after artifact upload without duplicate scoring; EX-04 fencing rejects stale finalization; orphan teardown works. | `docs/implementation/evidence/ENG-015/` (planned) |
| ENG-016 | Public website and comparison views | ENG-011, ENG-014 | BLOCKED | UI-01 accurately shows unknown/no-result states; UI-02 blocks invalid paired comparisons; tables are accessible and filters have permalinks. | `docs/implementation/evidence/ENG-016/` (planned) |
| ENG-017 | Admin campaigns and budget reservations | ENG-008, ENG-015, ENG-016 | BLOCKED | Draft/freeze/start/pause/resume/cancel transitions, reservations, role checks, and incomplete-campaign rules behave as specified. | `docs/implementation/evidence/ENG-017/` (planned) |
| ENG-018 | Publication, redaction, and corrections | ENG-011, ENG-014, ENG-016 | BLOCKED | PUB-01 rejects self-approval in official flow; PUB-02 retains withdrawn/corrected snapshots; public exports contain no hidden data. | `docs/implementation/evidence/ENG-018/` (planned) |
| ENG-019 | Official sandbox and threat-model tests | ENG-007, ENG-015 | BLOCKED | SE-02 denies and logs verifier/cloud-metadata access; no host, secret, hidden-label, or other-trial access; teardown and egress controls pass. | `docs/implementation/evidence/ENG-019/` (planned) |
| ENG-020 | Staging/production CI/CD, backups, and runbooks | ENG-014, ENG-015 | BLOCKED | Staging restore drill and compatible rollback pass; worker draining, reconciliation, observability, and least-privilege deployment are verified. | `docs/implementation/evidence/ENG-020/` (planned) |
| ENG-021 | Official holdout curation and protocol review | ENG-013 | BLOCKED | Family split, provenance/contamination review, evaluator review, sample-size decision, and independent admission review are complete. | Restricted review record plus `docs/implementation/evidence/ENG-021/summary.md` (planned) |
| ENG-022 | Official campaign and release | ENG-018, ENG-019, ENG-020, ENG-021 | BLOCKED | Complete prespecified cohort, required approvals, immutable publication/evidence, attrition disclosure, retention, and appeals process. | Immutable publication manifest (planned; no path assigned) |
| ENG-023 | Fixed reference model-track loop | ENG-009 | BLOCKED | Freeze tools, prompt, context policy, limits, provider behavior, and adapter; demonstrate reproducible reference-loop mechanics without stronger-model fallback. | `docs/implementation/evidence/ENG-023/` (planned) |
| ENG-024 | Model-track campaign | ENG-011, ENG-023 | BLOCKED | Run and publish a separate eligible cohort with fixed reference agent configuration and disclosed provider/control limitations. | Immutable model-track publication manifest (planned; no path assigned) |

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
