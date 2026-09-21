# Prompt 06 completion report — campaign and reliability protocol

Date: 2026-09-21

## Implemented functionality and changed files

- Added `packages/aieb-runner/src/aieb_runner/campaign.py` with immutable
  frozen campaign evidence, append-only attempt records, exact frozen-trial
  membership, first-valid-score selection, replacement retention, and
  canonical-ranking eligibility.
- Added separate Track A metrics for target behavior, hidden behavior,
  regression preservation, fault/recovery, cost, latency, steps, and tool use.
- Exported the protocol helpers from `aieb_runner`.
- Added `tests/test_campaign_protocol.py` covering invalid replacements,
  replacement non-improvement, incomplete cohorts, and out-of-plan attempts.

The existing Harbor backend remains the execution boundary. This pure protocol
layer consumes normalized backend artifacts and does not launch providers or
paid campaigns.

## Tests/commands actually run and results

- `python -m unittest tests.test_campaign_protocol tests.test_task_admission tests.test_v2_contracts tests.test_dev_bootstrap -v` — 14 passed.
- Python compilation and `git diff --check` — passed, apart from existing
  line-ending warnings.

## Acceptance gates

- Satisfied: frozen matrix envelope, append-only attempts, candidate versus
  infrastructure validity distinction, retained replacements, first scored
  outcome authority, incomplete-cohort ranking refusal, and separate Track A
  metrics.
- Pending: wiring this envelope into the PostgreSQL worker/Harbor dispatcher
  and adding full end-to-end clean replay/deadline evidence for every task.
- Blocked: paid campaigns, provider credentials, and official publication.

## Decisions or specification discrepancies

- Later valid replacements cannot improve an already-scored trial; all
  replacement attempts remain in evidence for cost/attrition analysis.
- No aggregate opaque score was introduced.

## Exact next command or numbered prompt

- Prompt 07 — build the defensible MVP-1 task suite.
