# Session handoff

Updated: 2026-09-14
Completed phase: Prompt 07 — ENG-008 role-separated accounting and ENG-009 local CLI

## Current state

ENG-008 and ENG-009 are complete for local deterministic RAG-01 development. `aieb-runner` has a receipt ledger separated by engineer, development application, verifier application, and verifier judge roles. It deduplicates broker and adapter observations by attempt/role/request/physical retry, preserves unknown billing after a lost response, and never turns unavailable usage into zero. Hard monetary enforcement is used only if a provider supplies a conservative reservation; otherwise it is explicitly `estimated_time_limited`.

The `aieb` local CLI supports `doctor`, `task validate`, `task verify`, `plan`, `run`, `resume`, `inspect`, and `report`. It writes versioned JSON output, frozen local state, JSONL events, immutable attempt evidence, and static HTML. The supported campaign scope is RAG-01 baseline/reference deterministic development candidates. A completed unsolved task is normal data and exits 0 unless `--fail-on-unsolved` is selected (exit 5). Local controller locking and frozen-manifest checking prevent concurrent or silently changed resumes.

No credential values are accepted in task/campaign documents or emitted to output/artifacts. The deterministic reference editor exists only to validate the vertical path; it is not real-agent compatibility evidence. Provider billing, hard cost reservations, and complete tool/cost traces are unavailable and visibly not claimed. ENG-001 real-agent smoke, ENG-019 official isolation, and RAG-01 independent human review remain blocked/pending as previously recorded.

## Commands

```powershell
uv sync --all-packages --locked
.\.venv\Scripts\aieb.exe --json --no-color doctor
.\.venv\Scripts\aieb.exe task validate suites\dev\rag.document-freshness
.\.venv\Scripts\aieb.exe plan --campaign examples\rag01-local-campaign.json
.\.venv\Scripts\aieb.exe run --campaign examples\rag01-local-campaign.json
.\.venv\Scripts\aieb.exe inspect --trial rag01-reference-local
.\.venv\Scripts\aieb.exe report --campaign rag01-reference-local --format html
.\.venv\Scripts\python.exe -m unittest tests.test_accounting_and_cli -v
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

Existing or future `AGENTS.md` instructions remain authoritative and must not be overwritten by this handoff.

## Recommended next prompt

Implement Prompt 08: ENG-010 additional extraction and tool-application task families through the existing CLI/execution/replay/report path. Do not broaden to hosted services or claim real-agent/provider evidence.
