# Prompt 04 completion report — Harbor boundary

Date: 2026-09-21

## Implemented functionality and changed files

The existing Harbor boundary was retained and verified rather than forked or
rewritten:

- `packages/aieb-runner/src/aieb_runner/backends/harbor/backend.py` remains the
  sole Harbor translation boundary and pins Harbor `0.22.0`.
- `toolchain.json` records Harbor source tag/commit, wheel digest, and the
  deterministic compatibility status.
- `scripts/run_eng001_spike.py` provides the deterministic Harbor trial path,
  including timeout collection, new-file artifacts, verifier output, and
  cleanup checks.
- `tests/integration/test_eng001_harbor.py` covers the real Docker path when
  explicitly enabled. Hidden evaluation uses a separate verifier environment;
  the backend refuses unsupported hardened-isolation requests.

No Harbor core code was modified. Provider calls and paid campaigns were not
run.

## Tests/commands actually run and results

- `python -m unittest tests.integration.test_eng001_harbor -v` — 1 test
  skipped because `AIEB_RUN_HARBOR_INTEGRATION` was not authorized/enabled.
- `python -m py_compile packages/aieb-runner/src/aieb_runner/backends/harbor/backend.py scripts/run_eng001_spike.py` — passed.
- v2 contract and bootstrap tests — 8 passed.
- The sandbox threat-model suite was attempted; 25 tests errored on this host's
  known Windows restriction against nested operations in freshly-created temp
  directories, before exercising the Harbor guards. This is an environment
  limitation, not reported as a product pass.

## Acceptance gates

- Satisfied: pinned Harbor adapter, no Harbor fork, AIEB-owned execution
  contracts, deny-by-default network/metadata controls, separate verifier
  environment declaration, deterministic compatibility harness, and artifact
  normalization path.
- Pending: real Docker compatibility run on a host with Docker and writable
  temporary-directory semantics.
- Blocked: real supported-agent/provider execution and official campaign
  authorization/credentials/cap remain unavailable.

## Decisions or specification discrepancies

- Prompt 04's real-agent gate remains explicitly blocked; the deterministic
  fixture is not presented as agent compatibility evidence.
- Existing lifecycle code is shared by the hosted worker and cannot yet be
  removed during cleanup; Harbor-backed replacement is a later refactor gate.

## Exact next command or numbered prompt

- Prompt 05 — task authoring and admission.
