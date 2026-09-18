# Corrections applied for Prompt 17 (ENG-023/ENG-024)

## Summary

This pass re-scoped the ENG-023/ENG-024 implementation plan after a codebase
verification pass found that the initial plan proposed rebuilding fields and
constraints that already exist in the codebase, and cited fabricated decimal
subsections of the architecture document.

## Changes made

### 1. `docs/implementation/DECISIONS.md` — corrected decision record

Appended the "ENG023 - Fixed reference model-track loop (plan corrected after
codebase verification)" decision. This record:

- **Acknowledges the P0 precondition** explicitly: architecture §18 ("Reference
  execution setup for the model track") states "Implement only after agent-track
  P0 works." P0 requires ENG-001's real installed-agent smoke, which is BLOCKED.
  The plan does NOT proceed silently — it mirrors `development-pilot-18.json`'s
  `state: "prepared-not-authorized"` + `execution_blocker` pattern.
- **Corrects fabricated citations**: §2.2→§2, §7.3→§13, §6.2/§6.3→§13/§14, §18
  is a real top-level section (not §18.3).
- **Re-scopes every "proposed addition" to reuse existing fields**:
  - `Track.AGENTS/MODELS` already exists (no new enum)
  - `EntrantRevision.engineer_model: ModelProfile` already exists (no new field)
  - `credential_ref_type: Literal["broker","direct","subscription"]` already exists
  - `UsageRequestRow.actor_role` DB CHECK constraint already enforces all four roles
  - `BudgetEnforcement.ESTIMATED_TIME_LIMITED` already exists in `accounting.py`
  - `BudgetReservationRow.enforcement` CHECK constraint already mirrors it in the DB
  - `ExecutionSpec.agent_import_path` already flows to Harbor's `AgentConfig.import_path`
  - `HarborBackend` already refuses hardened-only allocations (`PROVIDES_HARDENED_ISOLATION = False`)
  - `_find_unauthorized_host_mount()` already enforces verifier-isolation boundary
- **Adds the two missing tests** to the plan's acceptance scope: verifier-
  isolation boundary (citing `_find_unauthorized_host_mount`) and explicit
  no-silent-fallback-identity assertion.

### 2. `docs/implementation/evidence/ENG-023/README.md` — corrected evidence

New evidence doc covering: what already exists (table), what is genuinely new
(the model-provider reference coding loop, gated on P0), isolation reuse (model
loop = different `agent_import_path`), no-silent-fallback rule, and corrected
architecture citations.

### 3. `docs/implementation/evidence/ENG-024/README.md` — new evidence doc

Covers: Track separation (cohort_digest includes `track`), model-identity ledger
(existing `actor_role`), separate per-role budgets, frozen-campaign immutability,
non-comparable cross-release enforcement, credential protection (§14), execution
gating (`prepared-not-authorized`), and no-silent-model-fallback policy.

### 4. `examples/model-track-campaign.json` — frozen campaign manifest

A concrete, prepared-not-authorized manifest for a model-track campaign with a
single entrant (`claude-sonnet-4-20250514` as engineer model). Declares
`track: "models"`, `separate_cohort_from_agent_track: true`,
`no_silent_model_fallback: true`, `coverage_label: "estimated_time_limited"`, and
`state: "prepared-not-authorized"` with an explicit `execution_blocker` citing
§18's P0 gate.

### 5. `docs/implementation/STATUS.md` — updated ENG-023/ENG-024 rows

Both rows updated to reflect:
- The P0 precondition blocker (ENG-001)
- Codebase verification confirming existing fields
- The `prepared-not-authorized` / `execution_blocker` pattern
- Evidence paths pointing to the new docs

## What was NOT changed (and why)

- **No code changes**: all proposed fields already exist. Introducing duplicate
  schema fields (`track`, `engineer_model`, `coverage_label`, `actor_role`)
  would be harmful redundancy, not progress.
- **No new isolation boundaries**: the model track reuses Harbor entirely. The
  reference loop is a different `agent_import_path` in the existing
  `AgentConfig`, not a new backend mode.
- **No execution attempt**: P0/ENG-001 is BLOCKED. Running a model-track
  campaign without provider/model authorization would violate the "Do not
  provision paid infrastructure or deploy public services merely because this
  prompt describes them" constraint and the "use existing authorized cloud
  scope/budget only" rule.

## Key corrected understanding

The model track is NOT a new technical system to build. It is:
1. A **cohort filter** (`Track.MODELS`) that already exists
2. An **entrant configuration** (`engineer_model`/`ModelProfile`) that already
   exists
3. A **reference coding loop** (the only genuinely new code — a model-provider
   adapter + pinned tool-calling loop) — gated on P0/ENG-001

The original plan conflated "reuse existing entrant/track/cohort machinery"
(which is trivial wiring) with "build the reference model loop" (the actual
ENG-023 deliverable). The corrected plan separates these: Phase 1 = wire
existing fields (done, nothing to build); Phase 3 = the reference loop (gated).
