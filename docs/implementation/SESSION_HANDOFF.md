# Session handoff

Updated: 2026-09-14
Current phase: Prompt 10 — ENG-013 in progress (RAG-02 batch complete locally)

## Current state

ENG-010 is complete locally. EXT-02 (`ext.batch-alignment`) is a runnable HTTP extraction application with a deliberately broken positional mapping baseline. Its evaluator uses shuffled output, partial failure, and last-occurrence-wins repeated IDs to verify correspondence and retention. TOOL-01 (`tool.false-completion`) is a runnable HTTP workflow application with a deliberately dishonest completion baseline. Its evaluator owns an independent operation service and ledger; candidate logs do not decide outcomes. Both task families have public API/requirements, visible data, baseline/reference/alternative/shortcuts, provenance, maintainer-only fixtures, ten-reset controls, and pass through the CLI fresh replay path.

ENG-011 is complete locally. `aieb-analysis` is the authoritative metric implementation for per-task s/n, Wilson intervals, repeatability, complete-plan eligibility, cost-per-resolution unknown/zero-success behavior, time/deadline/attrition metrics, and project/family limitations.

ENG-012 is prepared but blocked. `examples/development-pilot-18.json` freezes an offline 3 task x 2 deterministic fixture entrant x 3 repetition matrix. It was not executed as a real pilot because no provider/cloud authorization, credentials, approved cap, or fixed real-agent configuration exists. It is not presented as a campaign result.

ENG-013 authoring now has twelve public runnable fixture applications. RAG-02/03/04, EXT-01/03/04, and TOOL-03/04 have complete local baseline/reference/alternative/shortcut controls and ten fresh local reference resets. TOOL-02's post-fix reference and ten resets pass; rerun its complete post-fix matrix before recording it as locally validated. `suites/dev/catalog.json` records twelve distinct synthetic family IDs; `aieb` has an explicit trusted evaluator/runtime mapping for each task; task-admission CI is in `.github/workflows/task-admission.yml`. These are local validation results, not official admission. Independent review remains pending for every task, so no admitted-only frozen suite release manifest is valid yet.

No real agent, paid provider, hosted service, deployment, publication, commit, or push occurred in this phase. Existing independent-review, ENG-001 real-agent, and ENG-019 official-isolation gates remain pending/blocked.

## Commands

```powershell
uv sync --all-packages --locked
.\.venv\Scripts\python.exe scripts\run_ext02_admission.py
.\.venv\Scripts\python.exe scripts\run_tool01_admission.py
.\.venv\Scripts\aieb.exe --json run --campaign examples\ext02-local-campaign.json
.\.venv\Scripts\aieb.exe --json run --campaign examples\tool01-local-campaign.json
.\.venv\Scripts\python.exe -m unittest tests.test_analysis tests.test_ext_tool_admission -v
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

## Recommended next prompt

Obtain independent task reviews before creating an admitted-only release manifest. Then freeze exact reviewed revisions, ordering, and weights. Do not claim an official campaign; ENG-012 remains blocked on provider/model authorization, credentials, and spend cap.
