# Session handoff

Updated: 2026-09-13  
Completed phase: Prompt 03 — ENG-002 core contracts, identities, and planning

## Current state

`packages/aieb-core` is a dependency-light core package with no Harbor, API, web, or execution imports. It contains strict Pydantic 2.13.5 contracts for the 14 requested domain envelopes, canonical JSON serialization/content hashes, YAML parsing only as an input convenience, and pure campaign reference resolution/freezing.

Core content identities use SHA-256 over sorted-key UTF-8 JSON with explicit nulls. Floating-point values, NaN, and Infinity are rejected; schema-owned decimal strings are normalized. The public contract rejects unsupported schema majors, unknown fields, duplicate requirements, floating official images, unsafe submission/candidate paths, invalid result envelopes, and duplicate frozen trial identities.

The planner resolves only known references, validates task/cohort/dependency/resource/capability compatibility, then deterministically expands and shuffles the task × entrant × repetition matrix. `CampaignDraft` remains editable input; `ResolvedCampaign` is a distinct frozen object. The 3 × 2 × 3 pilot test produces exactly 18 unique UUIDv5 trial identities.

ENG-002 is `COMPLETE`. Its evidence is in `docs/implementation/evidence/ENG-002/`. The only current ENG-001 blocker remains the explicitly unauthorized real installed-agent smoke; nothing in ENG-002 changes that status. No benchmark result, API, website, task admission, campaign execution, deployment, commit, push, or publication occurred.

## Commands

```powershell
uv sync --all-packages --locked
.\.venv\Scripts\python.exe scripts/generate_core_schemas.py
.\.venv\Scripts\python.exe -m unittest tests.test_core_contracts -v
uv lock --check
./dev.ps1 check
```

The final repository check passed 13 tests. The optional Docker compatibility test is skipped by default and was not rerun in this phase.

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

Implement ENG-003 only: a local content-addressed artifact store and safe extraction/replay boundary. Use the CandidateManifest contract, retain allowed new untracked files, reject escaping symlinks and unsafe file types, validate manifest paths/sizes/hashes, and add EX-01/SE-01 evidence. Do not create hosted storage, API, web, or a scientific task yet.
