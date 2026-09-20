# ENG-023 — Fixed reference model-track loop (corrected after codebase verification)

**Status:** the reference loop is now real, tested code (see "Review follow-up" below).
A LIVE campaign against a real paid provider remains gated on P0 / ENG-001, unchanged.

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

## Review follow-up: the reference loop is now real, tested code (2026-09-21)

An independent review correctly rejected the plan above as UNIMPLEMENTED: it found
`aieb_runner.model_loop:ModelTrackReferenceLoop` did not exist, did not import, and that
the "no silent fallback" behavior was documented, not executable. This section records
what was actually built to close that finding, and draws an exact line around what is
still genuinely blocked (nothing about ENG-001's P0 or ENG-024's live-campaign gate has
changed).

### What is now real

- **Provider-adapter layer**: `packages/aieb-runner/src/aieb_runner/model_providers/`
  - `base.py` — `ProviderMessage`, `ToolCall`, `ProviderResponse`, and a `ProviderError`
    hierarchy with distinct, attributable subclasses: `TransportError` and
    `RateLimitError` (retryable), `AuthenticationError` and `InvalidRequestError` (not
    retryable). A `ProviderAdapter` protocol with `complete()` and
    `unsupported_settings()`.
  - `fake.py` — `FakeProviderAdapter`: a fully deterministic, scriptable double (a queue
    of canned `ProviderResponse`/`ProviderError` values), zero network/credential
    dependency. Used by every unit test AND by the real Harbor Docker smoke test below.
    Also exposes a small process-wide registry (`FakeProviderAdapter.register`/
    `get_registered`) so a same-process caller can hand a Harbor-constructed agent
    instance a scripted adapter, since a live Python object cannot round-trip through
    `agent_import_path` — this is what lets the Docker smoke test exercise a genuine,
    deterministic multi-step tool-calling conversation with zero network calls.
  - `openai_compatible.py` — `OpenAICompatibleAdapter`: one real adapter for a standard
    `/chat/completions`-shaped endpoint, implemented with stdlib `urllib.request` only
    (no `openai`/`litellm`/`anthropic`/`httpx`/`requests` added as a dependency of
    `aieb-runner`, matching this project's existing convention of hand-rolling small
    stdlib clients — see ENG-020's Prometheus exporter). Reads its API key from an
    environment variable **at call time only**; never writes it to a file; never
    interpolates it into a shell command. Maps HTTP 401/403 → `AuthenticationError`, 429
    → `RateLimitError`, 400 → `InvalidRequestError`, network/5xx → `TransportError`.
- **The loop itself**: `packages/aieb-runner/src/aieb_runner/model_loop.py` —
  `ModelTrackReferenceLoop(BaseInstalledAgent)`, the same real Harbor contract
  `spike_agent.py` implements (`name()`, `install()`, `get_version_command()`,
  `async run(instruction, environment, context)`). Frozen module-level constants:
  `SYSTEM_PROMPT`, `TOOL_SCHEMAS` (list_files, read_file, search, patch, run_command,
  inspect_last_output, submit), `MAX_STEPS`, `MAX_RETRIES_PER_STEP`,
  `MAX_CONTEXT_CHARS`, `STEP_RETRY_BACKOFF_SECONDS`. The loop validates every tool call
  against `TOOL_SCHEMAS` (unknown tool / missing field / wrong type all rejected and fed
  back to the model as a structured error, bounded by `MAX_RETRIES_PER_STEP` before
  giving up cleanly), truncates context deterministically once it exceeds
  `MAX_CONTEXT_CHARS` (keeps the system prompt + task instruction + most recent turns;
  see the docstring on `_truncate_messages`), enforces its own wall-clock deadline each
  iteration independent of any external timeout, and on **every** stopping path
  (`submit`, step/retry/deadline exhaustion, or an unrecoverable provider error) copies
  whatever exists in `/workspace` into `/workspace/submission` and writes a structured
  `model_track_summary.json` (requested/reported model identity, `coverage_label`,
  step/retry/error counts) before returning — a trial that errors out still produces a
  scoreable candidate, never nothing. Requested/reported model identity and any
  unsupported settings are recorded through the real `aieb_runner.accounting.UsageLedger`
  (`UsageReceipt` per provider call, role `BudgetRole.ENGINEER`), not a parallel
  mechanism. Provider selection defaults to `FakeProviderAdapter` (no network, no
  credentials) unless a real provider is explicitly opted into via
  `AIEB_MODEL_TRACK_PROVIDER=openai_compatible` plus its config env vars — never a
  silent real network attempt.

### No-silent-fallback, made executable

`_LoopOutcome.coverage_label` is `"estimated_time_limited"` (never a silent full match)
whenever the provider's `reported_model` differs from the entrant's
`requested_model`, or the provider declares any `unsupported_settings` — exactly the
disclosed-profile semantics this document originally only described. Regression tests:
`test_reported_model_mismatch_is_disclosed_not_silent`,
`test_response_declaring_unsupported_settings_is_disclosed`,
`test_matching_reported_model_with_no_declined_settings_is_full_match`.

### Unit tests (no Docker, no network, no credentials)

`tests/test_eng023_model_loop.py` — 19 tests, all passing:
`.venv/Scripts/python.exe -m pytest tests/test_eng023_model_loop.py -q` → `19 passed`.
Covers tool-schema validation (well-formed accepted; unknown tool / missing field /
wrong type rejected), malformed-call eventual recovery AND exhaustion-with-candidate-
collection, deterministic context truncation, self-enforced deadline stop with candidate
collection, provider-error attribution for all four `ProviderError` subclasses
(retryable vs. not, and the exact class name recorded in the outcome), credential
protection (the adapter never persists its key on the instance; the loop never
interpolates a credential into an `environment.exec()` command string), complete
candidate collection under a forced mid-loop error, and the no-silent-fallback cases
above. The test double for `environment` (`_FakeEnvironment`) is a minimal object
implementing only the real `BaseEnvironment.exec()` this loop actually calls (confirmed
against harbor's real `environments/base.py`/`agents/installed/base.py` source before
writing it), shelling out to a real local bash so the loop's actual generated command
strings (find/head/grep/base64/cp) are genuinely exercised, not mocked away.

### Real Harbor Docker integration smoke test (executed, not simulated)

`scripts/run_eng023_model_loop_spike.py`, modeled on `scripts/run_eng001_spike.py`:
launches `HarborBackend` against real Docker, dispatches
`aieb_runner.model_loop:ModelTrackReferenceLoop` via
`ExecutionSpec.agent_import_path` — the exact mechanism the review's "critical finding"
said was broken — with the provider forced to a scripted `FakeProviderAdapter` (no
network, no credentials).

**Fixture note**: this uses a NEW minimal fixture,
`tests/fixtures/eng023_model_loop/task` (single "main" service,
`network_mode = "no-network"` throughout, no cross-service healthcheck), rather than
reusing `tests/fixtures/eng001_harbor/task`. That fixture's "main depends_on
application, gated by an HTTP healthcheck" topology turned out to be a poor fit on this
dev host: `HarborBackend`'s deny-by-default egress guard (`EgressGuardProxy`) routes
ALL container egress — even a plain intra-compose call from "main" to "application" —
through a proxy process running on the HOST, and "application" is a Compose-internal
DNS name that only resolves inside the compose network's own embedded DNS, never from
the host (confirmed directly: `socket.create_connection(("application", 8080))` from
this host raises `gaierror`). That made the eng001 fixture's own environment healthcheck
fail consistently regardless of which agent was under test — reproduced identically
against the pre-existing, unrelated `spike_agent` used by ENG-001 itself, so this is a
real, pre-existing environment limitation on this host, not a regression this work
introduced. Since the model-track loop needs no network at all by default, the new
fixture sidesteps the unrelated bug entirely instead of special-casing around it.

**Command and real captured output** (`D:\AI-Engineer-Bench\.venv\Scripts\python.exe scripts\run_eng023_model_loop_spike.py`), reproduced on two separate runs:

```json
{
  "agent_version": "aieb-model-track-reference-loop 0.1.0",
  "candidate_files": [
    "README.txt",
    "hello-from-model-track.txt",
    "model_track_summary.json"
  ],
  "cleanup_clean": true,
  "exception_info": null,
  "harbor_version": "0.22.0",
  "model_track_summary": {
    "coverage_label": "estimated_time_limited",
    "error_class": null,
    "malformed_call_count": 0,
    "provider_retry_count": 0,
    "reported_model": "fake-reference-model-v1",
    "requested_model": "spike-requested-model",
    "steps_taken": 4,
    "stop_reason": "submitted",
    "submitted": true,
    "unsupported_settings": [],
    "usage_receipts": [ /* 4 UsageReceipt entries, one per scripted provider call */ ]
  },
  "reward": 1.0,
  "state": "completed",
  "trial_dir": "D:\\AI-Engineer-Bench\\.cache\\eng023-runs\\eng023-63fc37ee7a"
}
```

(Second run: `trial_dir` `...\eng023-a054f4ada5`, identical `state`, `reward`, and
`model_track_summary` shape — the run is reproducible, not a one-off.) This is real,
executed evidence that `aieb_runner.model_loop:ModelTrackReferenceLoop` exists, imports,
installs, runs a full multi-step tool-calling loop (list → patch → run_command →
submit) dispatched through real `environment.exec()` calls inside a genuine Docker
container, produces a scoreable candidate, and scores `reward: 1.0` against a real
(separate-environment) verifier — directly answering the review's critical finding.

### What is explicitly NOT claimed

- `OpenAICompatibleAdapter` has never been exercised against a real, live paid
  model-provider endpoint. It is unit-tested only (schema/behavior, HTTP status
  mapping, credential handling) — never described as validated end-to-end against a
  real API.
- ENG-024's live-campaign authorization gate has not changed: provider credentials and
  an approved spend cap still do not exist in this environment. Only
  `examples/model-track-campaign.json`'s broken entrypoint and placeholder-style digests
  were fixed (see `docs/implementation/evidence/ENG-024/README.md`).
- ENG-001's P0 precondition is **not** claimed satisfied by this work. `STATUS.md` still
  records ENG-001 as `BLOCKED` (no provider/model authorization). ENG-023's CODE is now
  real and tested; the model track's live-campaign readiness still formally depends on
  P0/ENG-001 exactly as documented before this pass.
