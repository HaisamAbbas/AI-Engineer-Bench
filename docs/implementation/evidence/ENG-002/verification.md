# ENG-002 verification

Date: 2026-09-13  
Scope: versioned core contracts, canonical content identities, and pure campaign planning. No benchmark task, execution, hosted API, web application, provider call, or score was created.

## Delivered contracts

`aieb-core` defines strict versioned Pydantic models for TaskRevision, EntrantRevision, ProtocolRevision, BudgetProfile, Cohort, CampaignDraft, ResolvedCampaign, Trial, Attempt, CandidateManifest, EvaluationPlan, EvaluationResult, EventEnvelope, and PublicationManifest.

The models reject unknown fields, unsupported schema majors, non-finite/floating numeric values, duplicate requirement IDs, unresolved official image tags, unsafe submission paths, invalid candidate paths, duplicate campaign references, duplicate frozen trial IDs, invalid result envelopes, and invalid budget decimal values. Unknown usage is explicitly `null`; it is distinct from a zero count or a `"0"` cost. Execution validity and scientific verdict are separate fields, so infrastructure-invalid/cancelled results cannot masquerade as scientific failures.

Canonical identity is SHA-256 over UTF-8 JSON with sorted keys, explicit nulls, no floating-point values, and normalized decimal strings. It hashes resolved model values, never YAML formatting.

## Evidence

- CT-01 golden task digest: `bc01d9cf06e427c223acfb696a39f9097333da49f5363358e444358a8679b355`
- Equivalent YAML inputs: `examples/task-revision.yaml` and `examples/task-revision-reformatted.yaml`
- Realistic non-result examples: `examples/`
- Generated Draft 2020-12 schemas: `schemas/` (14 files)
- Behavioral tests: `tests/test_core_contracts.py`

The pure planner resolves references, checks task/cohort/profile/resource/capability compatibility, deterministically shuffles the full matrix by seed, assigns UUIDv5 campaign/trial identities from resolved content, and returns a frozen `ResolvedCampaign` distinct from the editable `CampaignDraft`. The test pilot of three tasks, two entrants, and three repetitions resolves to exactly 18 unique planned trials.

## Verified commands

```powershell
uv sync --all-packages --locked
.\.venv\Scripts\python.exe scripts/generate_core_schemas.py
.\.venv\Scripts\python.exe -m unittest tests.test_core_contracts -v
uv lock --check
./dev.ps1 check
```

Final result: 13 repository tests passed; the explicit Docker integration remains skipped unless requested by `AIEB_RUN_HARBOR_INTEGRATION=1`.
