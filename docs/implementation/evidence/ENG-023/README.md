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

## Review follow-up 2 (2026-09-21): per-trial-safe config wiring, fail-closed dispatch, real settings-digest verification

A second independent review accepted the loop's core mechanics but flagged three real
gaps in how it is *configured* and *accounted for*. This section records what was
actually fixed, and is explicit about the one gap that is genuinely NOT fixed (finding
2 below) — carried forward honestly rather than papered over.

### Finding 1 (HIGH) — campaign configuration was read from process-wide `os.environ`; now per-trial-safe constructor input

**The problem, precisely**: `HarborBackend.launch()` (`backends/harbor/backend.py`)
dispatches each trial as its own `asyncio.create_task(trial.run(), ...)` inside the
worker process — multiple trials, potentially with different requested models, can be
genuinely CONCURRENT in that one process. The previous implementation read
`AIEB_MODEL_TRACK_PROVIDER`/`AIEB_MODEL_TRACK_REQUESTED_MODEL`/`AIEB_MODEL_TRACK_BASE_URL`/
`AIEB_MODEL_TRACK_API_KEY_ENV_VAR` from `os.environ` inside `run()`. That is not merely
"not wired up yet" — it is actively racy across concurrent entrants sharing one process,
and it silently defaulted to a fake provider and `"unspecified"` model when unset.

**The fix**: Harbor already has a real, per-trial-safe injection mechanism.
`harbor.models.trial.config.AgentConfig` has `model_name: str | None` and
`kwargs: dict[str, Any]` fields; `harbor/agents/factory.py::create_agent_from_config`
constructs the agent as `agent_class(model_name=config.model_name, **agent_kwargs)`
where `agent_kwargs` includes `config.kwargs` — i.e. Harbor passes `model_name` and
arbitrary `kwargs` straight into the agent's `__init__` as ordinary per-INSTANCE Python
constructor arguments, with zero cross-trial race risk (unlike a process env var).

Concretely:
- `ExecutionSpec` (`packages/aieb-runner/src/aieb_runner/backends/base.py`) gained two
  additive fields: `model_name: str | None = None` and
  `agent_kwargs: dict[str, Any] = field(default_factory=dict)`. Both default to
  preserving today's behavior exactly for the agent track, which never sets them.
- `HarborBackend.launch()` (`backends/harbor/backend.py`) now threads
  `model_name=spec.model_name, kwargs=dict(spec.agent_kwargs)` into the `AgentConfig(...)`
  it already builds.
- `ModelTrackReferenceLoop.__init__` (`packages/aieb-runner/src/aieb_runner/model_loop.py`)
  now takes real constructor parameters instead of reading identity/config from
  `os.environ`: `provider_kind`, `requested_model` (falls back to `self.model_name`,
  which `BaseAgent.__init__` sets from Harbor's own `AgentConfig.model_name` — confirmed
  in `harbor/agents/base.py`), `base_url`, `api_key_env_var`, `settings`,
  `expected_settings_digest`, plus the pre-existing `provider=` direct-injection kwarg
  (still needed for tests/smoke, since a live Python object cannot round-trip through
  `AgentConfig.kwargs`, which must stay JSON-serializable).
- **Fail closed**: `_select_provider()` no longer has an implicit "fake" default for real
  dispatch. If the loop is constructed without a directly-injected `provider` AND without
  an explicit `provider_kind`, `run()` raises `RuntimeError` immediately, naming exactly
  what to pass. Similarly, `_resolve_requested_model()` raises `RuntimeError` if neither
  `requested_model` nor `self.model_name` is set and no provider was directly injected —
  no more silent `"unspecified"` for a real entrant. A test or smoke script that wants
  the fake, no-network provider must now say so explicitly, either
  `provider_kind="fake"` in `agent_kwargs` (exercises the real dispatch path) or direct
  `provider=FakeProviderAdapter()` injection (bypasses Harbor's config plumbing for pure
  unit tests) — nothing defaults there silently any more.
- `scripts/run_eng023_model_loop_spike.py` now configures the loop through this real
  mechanism — `ExecutionSpec(model_name="spike-requested-model",
  agent_kwargs={"provider_kind": "fake"}, ...)` — and was re-run against real Docker to
  prove it (see "Real Harbor Docker integration smoke test, re-run" below). No env var
  sets provider identity any more.
- `AIEB_MODEL_TRACK_DEADLINE_SEC` / `AIEB_MODEL_TRACK_MAX_STEPS` were deliberately KEPT as
  env-var testing hooks (not moved to kwargs): they bound a test's own wall-clock/step
  budget, not per-entrant identity or credential material, so they carry none of the
  cross-entrant race risk the removed identity env vars had — two concurrent trials
  reading the same deadline override is a shared testing knob, not a config collision.
  `AIEB_MODEL_TRACK_FAKE_SCRIPT_ID` was also kept: it hands a same-process caller's live,
  scripted `FakeProviderAdapter` Python object to a Harbor-constructed instance, which
  (like `provider=` direct injection) cannot round-trip through `AgentConfig.kwargs`
  either since that must stay a JSON-serializable dict.

### Finding 2 (HIGH) — usage is not connected to the authoritative accounting system: a genuine, PRE-EXISTING, cross-track gap, NOT fixed by this pass

This is written up honestly, not claimed fixed, because it cannot honestly be fixed from
inside this ticket. Verified directly (not taken on faith): `UsageRequestRow(` — the ORM
model a real usage write would construct — is instantiated ONLY in two places in this
entire repository: its own class definition in `services/api/src/aieb_api/models.py`,
and test fixtures in `tests/test_api_service.py`. There is **no production code path,
for the agent track OR the model track**, that ever writes a real `UsageRequestRow` from
an actual trial execution.

`aieb_runner.accounting.UsageLedger` (used by both the CLI, per ENG-008/009, and now this
loop) is a purely local, in-process ledger with no connection to `services/api`'s
database at all. Closing this gap for real would require an authenticated internal
worker-to-API usage-reporting endpoint (a "budget broker" identity) that has never been
built for any track — inventing an ad hoc, isolated DB write from inside
`model_loop.py` alone (which has no DB session, no HTTP client, and no service identity)
would be architecturally wrong, inconsistent with how every other part of this system
reports usage, and would leave the agent track equally unfixed while appearing to claim
ENG-023 solved something it does not own.

**This is not an ENG-023-specific defect.** It affects both tracks equally and predates
this ticket. It is recorded here as a real, separate, open blocker — not fixed by this
pass, and not something ENG-023 can close alone. It most naturally belongs to whichever
ticket is understood to own "the `usage_request` ledger is connected end to end" (no
ticket currently claims this explicitly; ENG-008's "Role-separated usage and caps" comes
closest but only ever implemented the LOCAL ledger, never a worker-to-API write path) —
flagging it here as a currently unowned gap rather than assigning it a ticket that was
never scoped to build a budget-broker service.

### Finding 3 (MEDIUM) — settings-digest enforcement, made real (verification, not decoding)

`aieb_core.models.ModelProfile` has `settings_digest: SHA256` but deliberately no raw
settings-payload field — the same freeze-by-digest pattern `EntrantRevision.prompt_digest`/
`tools_digest` already use (the real content lives elsewhere and is checked by
recomputing its digest, never by decoding the hash). The fix: the loop now accepts a
real `settings: dict[str, object]` payload via the constructor, alongside
`expected_settings_digest: str | None`. At the start of `run()`, if
`expected_settings_digest` is given, the loop computes
`hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()` and compares
it; a mismatch raises `RuntimeError` immediately (fail-closed — the same philosophy as
`scripts/build_release_bundle.py`'s `digest_mismatches` handling, never a silent
proceed). The REAL `settings` dict (no longer a hardcoded `settings = {}`) is then
threaded into both `provider.complete(messages, tools, settings)` and
`provider.unsupported_settings(settings)`, so unsupported-controls detection is now real
end-to-end, not only testable via a directly-injected fake response.

### Finding 4 — Windows `TemporaryDirectory` failures in the test suite, fixed with the ENG-021 idiom

A reviewer reported "6 tests passed; 13 tests failed/error during TemporaryDirectory
workspace creation/cleanup with WinError 5" running `tests/test_eng023_model_loop.py`.
This matches an already-solved failure class (`tests/test_eng021_bundle_exclusions.py`,
rounds 2–4 in `STATUS.md`/`DECISIONS.md`): some Windows hosts cannot do nested
create/delete inside a directory a process just created, even though
`tempfile.mkdtemp()`/`TemporaryDirectory()` itself succeeds. `tests/test_eng023_model_loop.py`
now reuses the exact same idiom, copied verbatim rather than reinvented:
`_make_test_tmp_dir()` (prefers a repo-relative `.cache/test-tmp` over the OS global temp
root, honoring `AIEB_TEST_TMP_ROOT`), `_tmp_dir_supports_nested_ops()` (probes
create/write/rmtree on a child path and returns a precise skip reason instead of a
misleading failure), and `_rmtree_windows_safe()` (clears the read-only bit and retries
on `PermissionError` during cleanup). Both `ModelTrackReferenceLoopTest.setUp` and the
two new test classes below use this pattern instead of raw
`tempfile.TemporaryDirectory()`.

### Regression test counts (this environment)

`D:\AI-Engineer-Bench\.venv\Scripts\python.exe -m pytest tests/test_eng023_model_loop.py -q`
→ before this pass: `19 passed`; after: **`28 passed`** (9 new tests: 4 fail-closed
provider/model-identity tests, 2 fail-closed/positive settings-digest tests, 1 real
settings-threading test, plus 2 tests confirming the `provider_kind`/`model_name`
config-wiring path dispatches cleanly end to end without any direct `provider=`
injection).

`D:\AI-Engineer-Bench\.venv\Scripts\python.exe -m pytest tests/test_eng019_sandbox_threat_model.py tests/test_accounting_and_cli.py -q`
→ `45 passed` (no regressions in the shared `ExecutionSpec`/accounting code the
`ExecutionSpec.model_name`/`agent_kwargs` fields touch).

### Real Harbor Docker integration smoke test, re-run after the wiring change

`scripts/run_eng023_model_loop_spike.py` was updated to configure the loop through the
real mechanism (`ExecutionSpec(model_name="spike-requested-model",
agent_kwargs={"provider_kind": "fake"}, ...)`, no env vars for provider identity) and
re-executed against real Docker. Real captured output:

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
  "trial_dir": "D:\\AI-Engineer-Bench\\.cache\\eng023-runs\\eng023-a9250a077c"
}
```

This is the concrete, executed proof that the new per-trial-safe config mechanism works
through the real Harbor dispatch path, not just in isolated unit tests:
`requested_model: "spike-requested-model"` came from `ExecutionSpec.model_name` →
`AgentConfig.model_name` → `self.model_name` (no env var), and the fake provider was
selected via `agent_kwargs={"provider_kind": "fake"}` → `AgentConfig.kwargs` → the
constructor's `provider_kind` parameter (no env var either). `coverage_label` is
`estimated_time_limited` because the scripted `reported_model`
(`"fake-reference-model-v1"`) legitimately differs from the requested one — exactly the
disclosed-profile behavior this loop is supposed to produce, not a defect.

### Still explicitly NOT claimed (unchanged, plus one new item, up to this point)

Everything in "What is explicitly NOT claimed" above still holds. In addition: **usage
accounting is not connected to `services/api`'s authoritative ledger for either track**
(Finding 2 above) — this was true before ENG-023 and remains true after it; it is not
presented as something this pass fixed.

**This is superseded below** — a third review follow-up closes Finding 2 for real
(the writer functions and the loop's hook into them), while explicitly NOT closing the
separate, larger "a production dispatcher actually invokes this pipeline for a live
campaign" gap. Read the next section for the precise boundary.

## Review follow-up 3 (2026-09-21): usage accounting closed for real — new schema, real DB writers, real Harbor-Docker-to-Postgres proof

A third review held firm on Finding 2 above: "Usage accounting is still not implemented.
The loop's `UsageLedger` remains in-process and serializes receipts into
`model_track_summary.json`; it does not create authoritative API
`UsageRequestRow`/`UsageReceiptRow` records or reconcile provider costs/budget coverage.
Requested/reported identity likewise is not persisted into the authoritative usage
ledger." This section records what was actually closed, precisely, and draws the same
honest line the two prior follow-ups drew around what remains genuinely blocked.

### What this pass verified before writing any code

- `UsageRequestRow(` was, before this pass, constructed ONLY in
  `services/api/src/aieb_api/models.py`'s own class definition and in
  `tests/test_api_service.py` fixtures — confirmed again by grep across the whole repo.
  No production code path, for either track, ever wrote a real usage row.
- `services/api/src/aieb_api/worker/loop.py` does not import `aieb_runner` at all — there
  is no production glue anywhere that has a worker process call
  `aieb_runner.backends.harbor.HarborBackend.launch()` for a real trial. Only manual spike
  scripts (`scripts/run_eng001_spike.py`, `scripts/run_eng023_model_loop_spike.py`) ever
  call `HarborBackend.launch()`. Building that full worker-to-Harbor production dispatcher
  is real, separate, substantial work — it is properly ENG-001's remaining P0 scope (a
  real installed-agent smoke, gated on provider/model authorization), not something this
  pass builds or should build.
- `UsageRequestRow`/`UsageReceiptRow` (`services/api/src/aieb_api/models.py`, pre-existing)
  had no field anywhere for per-attempt requested/reported model identity — a real,
  additional schema gap beyond "just call an existing writer."

### What is now real

**New table, migration `b3f1c2a9d4e7`** (`down_revision = "c7d8e9f0a1b2"`, the verified
head at the time this migration was written):
`services/api/src/aieb_api/migrations/versions/b3f1c2a9d4e7_attempt_model_identity.py`
adds `attempt_model_identity` — `id` (uuid pk), `attempt_id` (FK to `attempt.id`, nullable
to match `usage_request.attempt_id`'s existing nullability), `actor_role` (same CHECK
vocabulary as `usage_request.actor_role`), `requested_model` (not null), `reported_model`
(nullable), `settings_digest` (nullable), `coverage_label` (not null — the same
`estimated_time_limited`/`full_match` vocabulary `_LoopOutcome.coverage_label` already
produces), `created_at`. Unique constraint `uq_attempt_model_identity_attempt_role` on
`(attempt_id, actor_role)`, mirroring `usage_request`'s
`uq_usage_request_scoped_identity` "record once per identity" idiom. The corresponding
ORM class is `AttemptModelIdentityRow` in `services/api/src/aieb_api/models.py`.
Verified round-trip against the real test Postgres:
```
alembic upgrade head    # c7d8e9f0a1b2 -> b3f1c2a9d4e7
alembic downgrade -1    # b3f1c2a9d4e7 -> c7d8e9f0a1b2
alembic upgrade head    # c7d8e9f0a1b2 -> b3f1c2a9d4e7
alembic current          # b3f1c2a9d4e7 (head)
```
all four commands executed for real against `AIEB_DATABASE_URL` for this pass, clean.

**New real DB-writing repository functions**, `services/api/src/aieb_api/worker/repository.py`
(same module ENG-015's leasing/fencing logic already lives in — no ad hoc queries
elsewhere in this codebase, and this follows that same rule):
- `record_usage_receipts(session, *, attempt_id, actor_role, receipts, commit=True)` —
  inserts one real `UsageRequestRow` per distinct `request_id` and one real
  `UsageReceiptRow` per physical retry under it. Idempotent on a unique-constraint
  conflict using the SAME established idiom `record_candidate`/`record_evaluation`
  already use elsewhere in this file: catch `IntegrityError`, re-select the existing row,
  accept a byte-for-byte-identical replay silently, and raise a dedicated conflict error
  (`UsageRequestConflictError` / `UsageReceiptConflictError`) on any genuine mismatch —
  never silently misrepresenting what is actually persisted.
- `record_model_identity(session, *, attempt_id, actor_role, requested_model,
  reported_model, settings_digest, coverage_label, commit=True)` — inserts the real
  `AttemptModelIdentityRow`, idempotent on `(attempt_id, actor_role)` the same way
  (`ModelIdentityConflictError` on mismatch).

These two functions are now the ONLY production code path in this repository that ever
inserts a real `UsageRequestRow`/`UsageReceiptRow`/`AttemptModelIdentityRow`.

**The loop's hook into them**, `packages/aieb-runner/src/aieb_runner/model_loop.py` —
`aieb_runner` still has ZERO dependency on `aieb_api`/`services/api` (monorepo layering
preserved exactly as before). `ModelTrackReferenceLoop.__init__` gained
`usage_sink: Callable[[dict], None] | None = None`, the same category of parameter as the
pre-existing `provider=` direct-injection kwarg. For real Harbor dispatch, where a live
callable cannot round-trip through Harbor's serializable `AgentConfig.kwargs` any more
than a live provider object can, the SAME registry-by-id seam
`FakeProviderAdapter.register`/`get_registered` already established is mirrored exactly:
`register_usage_sink`/`get_registered_usage_sink` plus `AIEB_MODEL_TRACK_USAGE_SINK_ID`
(`ENV_USAGE_SINK_ID`), resolved by `_select_usage_sink()` in the same
direct-injection-first, then-registry order as `_select_provider()`. In `run()`'s
existing `try/finally` (the same one that already guarantees `_finalize_submission` runs
on every stopping path — submit, `MAX_STEPS` exhaustion, deadline, malformed-call
exhaustion, or an unrecoverable provider error), if a sink is configured it is called
exactly once with a plain dict: `actor_role` (always `"engineer"` for this loop),
`requested_model`, `reported_model`, `settings_digest` (the real
`expected_settings_digest` the loop was constructed with), `coverage_label`, and
`usage_receipts` (reusing `outcome.usage_receipts`, already built — no new accounting
mechanism). **No sink configured is today's exact prior behavior, unchanged**: only
`model_track_summary.json` is written, fully backward compatible.

New dependency-free unit test class `UsageSinkHookTest` in `tests/test_eng023_model_loop.py`
proves the hook fires exactly once on every stopping path (submit, deadline,
malformed-call exhaustion, unrecoverable provider error) with a simple list-appending fake
sink, plus that no-sink-configured behavior is unchanged and that the registry-by-id
resolution path works — no DB, no Docker.

### The real end-to-end proof (not a mock)

`scripts/run_eng023_model_loop_spike.py` now additionally: registers a real usage-sink
callback via the registry-by-id mechanism (`_register_usage_sink`); that callback opens a
real session against the real test Postgres (`AIEB_DATABASE_URL`) and calls
`record_usage_receipts`/`record_model_identity` — the exact same production writer
functions above, not a mock or a duplicate code path; runs the model-track loop through
REAL Harbor Docker dispatch exactly as every prior run in this document (scripted
`FakeProviderAdapter`, zero network, zero real spend); and, AFTER Harbor's Docker
teardown (`backend.cleanup()`) has already completed, opens a FRESH session and
independently re-queries the real database, asserting the expected rows exist with the
right values. `attempt_id=None` was chosen deliberately for this spike (documented in the
script itself): building a full campaign/trial/attempt fixture chain just to attach the
spike's usage rows to is orthogonal to what this spike proves (the
loop → sink → repository → real Postgres pipeline itself), and `attempt_id` is
schema-legal as `NULL` for exactly this case on both `usage_request` (pre-existing) and
the new `attempt_model_identity` table. (The new repository-function test suite below
*does* exercise the full real-`Attempt`-row path, so that path is independently proven
too — the spike script's choice of `NULL` is a scope decision for the spike, not a
limitation of the writers.)

Real captured output (`D:\AI-Engineer-Bench\.venv\Scripts\python.exe scripts\run_eng023_model_loop_spike.py`,
`AIEB_DATABASE_URL` pointed at the real disposable test Postgres, exit code `0`):

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
    "reported_model": "fake-reference-model-v1",
    "requested_model": "spike-requested-model",
    "stop_reason": "submitted",
    "submitted": true,
    "usage_receipts": [ /* 4 UsageReceipt entries, one per scripted provider call */ ]
  },
  "reward": 1.0,
  "state": "completed",
  "usage_accounting": {
    "identity_coverage_label": "estimated_time_limited",
    "identity_reported_model": "fake-reference-model-v1",
    "identity_requested_model": "spike-requested-model",
    "identity_row_found": true,
    "identity_settings_digest": null,
    "usage_receipt_rows_found": 4,
    "usage_request_rows_found": 4
  }
}
```

`usage_accounting` is populated by a query issued AFTER `backend.cleanup()` — a fresh
session, independent of anything held open during the run — against the real
`AIEB_DATABASE_URL` test database. `identity_row_found: true` with the expected
`requested_model`/`reported_model`/`coverage_label` and `usage_request_rows_found: 4` /
`usage_receipt_rows_found: 4` (one request/receipt pair per scripted provider call —
list_files, patch, run_command, submit) is the concrete proof that a real Harbor Docker
run's usage flowed into real Postgres rows, independently queried back out — not
serialized only into `model_track_summary.json` inside the container.

### Repository-function tests (real Postgres, full real-Attempt-row path)

`tests/test_eng023_usage_accounting_repository.py` — 10 tests, all passing against the
real test Postgres:
`.venv/Scripts/python.exe -m pytest tests/test_eng023_usage_accounting_repository.py -q`
→ `10 passed`. Covers, for both `record_usage_receipts` and `record_model_identity`: a
normal insert (using the full real campaign→trial→attempt fixture chain, the same minimal
pattern `tests/test_metrics.py::_seed_minimal_engineering_lease` established, not the full
cohort-freezing pipeline), multiple physical retries under one logical request, idempotent
replay with identical content, a genuine conflict on replay with different content
(`UsageRequestConflictError`/`UsageReceiptConflictError`/`ModelIdentityConflictError`),
and the CHECK constraint on `actor_role` rejecting an invalid value.

### Full test run for this pass

- `tests/test_eng023_model_loop.py` — 34 passed (the pre-existing 28, plus 6 new
  `UsageSinkHookTest` cases).
- `tests/test_eng023_usage_accounting_repository.py` — 10 passed (new).
- `tests/test_eng019_sandbox_threat_model.py tests/test_accounting_and_cli.py
  tests/test_worker_leasing.py` — 92 passed, no regressions from the new migration/model.

### Precisely what is closed, and what is NOT (read this carefully)

**Closed for real**: real `UsageRequestRow`/`UsageReceiptRow` records now exist and are
provably populated by a real end-to-end run — loop → `usage_sink` → real repository
functions → real Postgres rows, verified via real Harbor Docker execution independently
querying the database back afterward. A new `attempt_model_identity` table (migration
`b3f1c2a9d4e7`) now persists requested/reported model identity into the authoritative
database, closing the specific finding that "requested/reported identity likewise is not
persisted into the authoritative usage ledger."

**NOT claimed, and should not be inferred**:
- **No live campaign has run**, and this pipeline is **not wired into a production
  dispatcher**. `services/api/src/aieb_api/worker/loop.py` still does not import
  `aieb_runner` and still never calls `HarborBackend.launch()` for any track — that
  dispatcher does not exist yet for ANY track, agent or model. Building it is out of this
  pass's scope; it is properly ENG-001's remaining P0 work, gated on the same
  provider/model authorization ENG-001 has always required. **The writer functions and
  the loop's hook into them are real and proven end-to-end; what remains is a production
  dispatcher invoking this pipeline for a live campaign, which needs the same
  provider/model authorization ENG-001's P0 has always required — this pass does not
  change that gate.**
- ENG-024's live-campaign authorization gate is unchanged: provider credentials and an
  approved spend cap still do not exist in this environment.
- ENG-001's P0 status is unchanged. `STATUS.md` was re-checked (not assumed) and its
  ENG-001 row still reads `BLOCKED` — this pass did not touch it.
- No live, paid-provider execution occurred anywhere in this pass. The scripted
  `FakeProviderAdapter` (zero network, zero credentials, zero spend) is the only provider
  exercised, exactly as in every prior ENG-023 smoke run in this document.
- No new third-party dependency was added; the migration/repository/loop changes use only
  SQLAlchemy/Alembic/stdlib already present in this codebase.
