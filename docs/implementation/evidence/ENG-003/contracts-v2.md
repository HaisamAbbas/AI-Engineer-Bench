# Prompt 03 completion report — benchmark contracts

Date: 2026-09-21

## Implemented functionality and changed files

- Added `packages/aieb-core/src/aieb_core/contracts_v2.py` with strict,
  extra-forbidden v2 contracts for task revisions, evaluator revisions,
  Track A/Track B protocols, agent/model configurations, complete campaign
  matrices, release manifests, verdicts, and immutable publication snapshots.
- Added exports in `packages/aieb-core/src/aieb_core/__init__.py`.
- Added golden and negative vectors in `tests/test_v2_contracts.py`.

The v1 models remain unchanged for digest compatibility. Harbor task.toml
compatibility remains behind `aieb_runner.backends.harbor`; this phase adds
contract validation and does not dispatch campaigns or call providers.

## Tests/commands actually run and results

- `python -m unittest tests.test_v2_contracts tests.test_dev_bootstrap -v`
  — 8 tests passed.
- `git diff --check` — passed, apart from pre-existing line-ending warnings.

## Acceptance gates

- Satisfied: unknown schema/fields rejected; immutable image digests required;
  safe paths, license/provenance, evaluator/protocol identity, complete
  campaign cells, separate correctness/reliability metrics, and Track A/B
  protocol identity validated.
- Pending: migration adapters for persisted v1 rows and integration into the
  operator CLI/release preparation flow.
- Blocked: official holdouts, provider execution, paid campaigns, and
  publication remain authorization-gated.

## Decisions or specification discrepancies

- New v2 contracts are additive to preserve valid v1 frozen-manifest digests.
- `PublicationSnapshot` intentionally exposes correctness and reliability as
  separate fields; no universal opaque score was introduced.

## Exact next command or numbered prompt

- Prompt 04 — implement and verify the pinned Harbor adapter execution path.
