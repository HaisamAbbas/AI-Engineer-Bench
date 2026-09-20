# ENG-024 — Model-track campaign (corrected after codebase verification)

**Status:** planned (gated on ENG-023 / P0)

A model-track campaign is a separate eligible cohort per architecture section 2
("Evaluation subject and tracks"): same tasks, same isolation, same evaluators,
but with a fixed reference coding-agent loop where only the engineer model
varies. Model-track scores must NOT be merged with agent-track scores.

This document records the corrected plan after a codebase verification pass
found that the initial plan proposed adding fields that already exist.

## What already exists (no rebuild needed)

- **Track separation**: `Track(str, enum)` with `AGENTS`/`MODELS` already in
  `packages/aieb-core/src/aieb_core/models.py`. A model-track campaign selects
  entrants whose `entrant_revision.tracked == "models"`; agent-track entrants
  are `"agents"`. These never mix in one publication's evidence manifest.
- **Cohort digest**: `ResolvedCampaign.cohort_digest` already separates cohorts
  by `Cohort.digest()` (which includes `track`). A model-track cohort and an
  agent-track cohort have different digests by construction.
- **Model identity ledger**: `UsageRequestRow.actor_role` CHECK constraint
  already records `engineer`, `dev_application`, `verifier_application`,
  `verifier_judge` — the four roles that cover engineer_model,
  application_model, and verifier_judge_model identities.
- **Separate budgets per role**: `BudgetProfileV2` / `RoleBudget` already
  allocates per-role budgets. Engineer model calls use an authenticated
  budget broker; application models use another identity and budget; verifier/
  judge calls use a third (section 14).
- **Frozen campaign immutability**: `BEFORE UPDATE` triggers on campaign
  manifest fields (ENG-014/AUDIT-002) already prevent post-freeze changes to
  entrant/track/cohort identity.
- **Non-comparable cross-release**: `GET /v1/comparisons` already treats
  cross-publication comparisons as unconditionally non-comparable (ENG-016-007).
  A model-track publication and an agent-track publication are different
  publications and therefore never paired — enforced structurally, not by
  naming convention.

## What the campaign manifest declares

`examples/model-track-campaign.json` (frozen, not executed):

- `track: "models"` — cohort-level track selector
- `entrants` — each with `engineer_model.requested_model` (single model, no
  fallback), `engineer_model.settings_digest`, and
  `credential_ref_type`
- `cohort_digest` — differs from any agent-track cohort by construction
- `budget_profile` — `estimated_time_limited` enforcement (no provider
  reservation integration; §19 / ENG-008)
- `state: "prepared-not-authorized"` and `execution_blocker: "P0 precondition
  (§18) not met: ENG-001 real installed-agent smoke blocked on provider/model
  authorization"`

## Separate accounting and cohort identity

- **Cohort ID**: model-track results use a distinct `cohort_digest` (includes
  `track: "models"`). Aggregation (`services/api/aggregation.py`) derives
  `per_category`/`per_entrant` from the same per-task cells as the agent track,
  but never blends the two.
- **Usage records**: `UsageRequestRow.actor_role` records the engineer model's
  identity under `actor_role='engineer'`, application model under
  `actor_role='dev_application'`, verifier/judge under `actor_role='verifier_judge'`.
- **No silent model fallback**: each entrant specifies a single
  `ModelProfile.requested_model`. If the provider/adapter cannot honor
  `settings_digest`, the entrant gets `coverage_label: "estimated_time_limited"`
  (via `BudgetEnforcement.ESTIMATED_TIME_LIMITED`), not a false match to a
  stronger model. Retries are bounded and count against the engineering budget.

## Credential protection

Per section 14, no provider secret appears in the task image. Credentials are
injected at the worker level via environment variables, never in `task.yaml`
or candidate artifacts. `credential_ref_type` on the entrant revision
declares the credential shape (`broker`/`direct`/`subscription`); the actual
credential material is resolved at dispatch from the operator's secret store,
bound to the engineer model's authenticated budget broker identity.

## Execution gating

The manifest is prepared only under existing explicit spend authorization. If
blocked (no provider/model authorization, no approved cap), it carries
`state: "prepared-not-authorized"` with an explicit `execution_blocker`,
exactly mirroring `examples/development-pilot-18.json`.

## Review follow-up (2026-09-21): manifest defect fixed, gate unchanged

An independent review flagged that `examples/model-track-campaign.json` pointed its
`agent_import_path`/`agent_implementation` at a module that did not exist
(`aieb_runner.model_loop:ModelTrackReferenceLoop`), and that some digests looked like
placeholders. ENG-023 (see `docs/implementation/evidence/ENG-023/README.md`) now makes
that module real, tested code with an executed real-Docker proof. This entry records
only the corresponding manifest fix — it does **not** change this ticket's own gate.

- Fixed `agent_implementation` (it read `aieb-runner.model_loop:...` with a hyphen,
  which is not a valid Python module path) to the correct, real, importable
  `aieb_runner.model_loop:ModelTrackReferenceLoop` — the same value
  `agent_import_path` already had.
- Replaced `prompt_digest`/`tools_digest` with real sha256 digests of
  `model_loop.py`'s frozen `SYSTEM_PROMPT` string and `TOOL_SCHEMAS` tuple
  (`json.dumps(TOOL_SCHEMAS, sort_keys=True)`), computed the same way
  `scripts/compute_task_digests.py` hashes other real on-disk content elsewhere in this
  repo — not invented, not placeholders.
- Everything else in the manifest is unchanged, including `state:
  "prepared-not-authorized"` and its `execution_blocker` field, verbatim.

**Unchanged, and must stay unchanged**: this manifest has never been executed against a
real provider, and is not being executed now. Provider credentials and an approved
spend cap still do not exist in this environment. `STATUS.md`'s ENG-001 row is still
`BLOCKED` for the same reason it always was (no provider/model authorization) — that is
a separate precondition from "does the model-track loop's code exist and work," which
ENG-023's real Harbor Docker run now answers yes to. Fixing the manifest's broken
entrypoint reference is a defect fix, not a readiness claim for a live campaign.
