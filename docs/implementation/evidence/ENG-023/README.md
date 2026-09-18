# ENG-023 — Fixed reference model-track loop (corrected after codebase verification)

**Status:** planned (gated on P0 / ENG-001)

This document records the corrected plan for Prompt 17's model-track reference
loop, after a codebase verification pass found that the initial plan proposed
rebuilding fields that already exist. The plan is re-scoped to wire existing
generic infrastructure rather than introduce redundant schema.

See `docs/implementation/DECISIONS.md` § "ENG023 - Fixed reference model-track
loop (plan corrected after codebase verification)" for the full decision
record.

## What already exists (no rebuild needed)

| Proposed addition | Real location | Status |
|---|---|---|
| `track: agent \| model` field | `packages/aieb-core/src/aieb_core/models.py` — `Track(StrEnum)` with `AGENTS`, `MODELS` | Exists |
| `engineer_model` configuration | `EntrantRevision.engineer_model: ModelProfile` (provider_class, requested_model, reported_model, settings_digest) | Exists |
| `credential_ref_type` | `EntrantRevision.credential_ref_type: Literal["broker","direct","subscription"]` | Exists |
| Actor-role DB constraint | `services/api/models.py` `UsageRequestRow.actor_role` CHECK `in ('engineer','dev_application','verifier_application','verifier_judge')` | Exists |
| `coverage_label: estimated_time_limited` | `aieb_runner/accounting.py` `BudgetEnforcement.ESTIMATED_TIME_LIMITED` + `services/api/models.py` `BudgetReservationRow.enforcement` CHECK | Exists |
| Model-loop entrypoint | `backends/base.py` `ExecutionSpec.agent_import_path` → Harbor `AgentConfig(import_path=...)` | Exists |
| Verifier-isolation enforcement | `backends/harbor/backend.py` `_find_unauthorized_host_mount()` + `PROVIDES_HARDENED_ISOLATION=False` | Exists |
| Engineer model identity in API | `services/api/routes/authorized.py` `_configuration_projection()` exposes `requested_model`, `reported_model`, `settings_digest` | Exists |

## What is genuinely new (the actual model loop)

The only non-existing piece is the **reference coding-agent loop** that calls a
model provider — this is ENG-023's actual deliverable. It is gated behind
section 18 ("Reference execution setup for the model track"): "Implement only
after agent-track P0 works." P0 requires ENG-001's real installed-agent smoke,
which is BLOCKED.

If blocked: campaign manifest uses `state: "prepared-not-authorized"` with an
explicit `execution_blocker` field, mirroring `examples/development-pilot-18.json`.

## Corrected architecture citations

| Plan's citation | Real section |
|---|---|
| §2.2 (model track) | §2 — "Evaluation subject and tracks" |
| §7.3 (Harbor boundary) | §13 — "Harbor integration boundary" |
| §6.2 (artifact-replay) | §13 (Harbor owns execution; AIEB owns verification) |
| §6.3 / §37 (credentials) | §14 — "Application models and external dependencies" |
| §37 (no Docker socket) | §37 context — "Security and operational boundaries" |
| §18 (reference execution gate) | §18 — "Reference execution setup for the model track" |

## Isolation reuse

The model track reuses Harbor entirely. The reference loop is dispatched as a
different `agent_import_path` in the existing `AgentConfig`, not a new backend
mode. Same Docker sandbox, same egress guard, same hard-mount checks. No new
isolation boundaries.

## No silent model fallback

Per §2 and §18: each model-track entrant specifies a single model in
`ModelProfile.requested_model`. If a provider does not expose a control the
entrants's `settings_digest` claims, the entrant gets a disclosed profile
(coverage `estimated_time_limited`), not a false match. Retries are bounded and
count against the engineering budget.

## Acceptance tests

1. **Verifier-isolation boundary test** — the existing test suite already
   covers `_find_unauthorized_host_mount` thoroughly in
   `tests/test_eng019_sandbox_threat_model.py::UnauthorizedHostMountDetectionTest`
   (10 tests covering Docker socket mount, arbitrary host path, hidden fixture
   directory, relative `../../` escape, Docker's canonical `compose.yaml`
   filename, named volumes, and in-task bind mounts). No new test needed;
   the model track inherits this protection since it uses the same Harbor
   backend and `ExecutionSpec.agent_import_path`.

2. **No-silent-fallback-identity test** — a model-track entrant whose
   `ModelProfile.requested_model` maps to a provider that does not expose a
   setting the entrant's profile claims produces a disclosed profile (not a
   false match). This is enforced at the `agent_import_path` adapter level:
   if the provider cannot honor `settings_digest` or returns a different
   `reported_model`, the entrant's coverage is labeled
   `estimated_time_limited` (via `BudgetEnforcement.ESTIMATED_TIME_LIMITED`)
   and the discrepancy is surfaced in the `usage_request` ledger via
   `actor_role='engineer'`, not silently approximated. Test: assert that an
   entrant with `no_silent_model_fallback: true` and a mismatched
   `settings_digest` produces `reported_model != requested_model` in the
   usage ledger with `coverage_label: "estimated_time_limited"`, never a
   silently-substituted stronger model.
