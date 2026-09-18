# ENG-011 aggregation review corrections

Date: 2026-09-17
Status: COMPLETE after independent technical review on 2026-09-18. The review accepted the PostgreSQL-backed frozen-manifest membership, replacement retention, validity, deadline, and unknown-cost regressions. This acceptance does not authorize an official campaign or publication.

## Corrections (round 2 — frozen-manifest membership)

- `campaign_observations`/`aggregate_campaign_snapshot` now require an exact bijection between persisted trials and the frozen matrix before reading any outcome: trial IDs (missing/extra/duplicate rejected), repetition index, `cell_digest` recomputed from the full `Trial` contract, revision slug/version, and canonical task/entrant manifest digests. Any mismatch raises `CampaignNotAggregatable` — fail closed, no partial snapshot. A duplicate/incomplete frozen matrix (missing/empty/partial trials, duplicate trial IDs or cells, unknown task/entrant digests) is also rejected.
- `TrialRow.id` is now set from the frozen trial's UUID (as the production enqueuer does), so fixtures exercise real identity checking.

## Regression evidence (round 2)

Three new PostgreSQL tests: an otherwise-valid unplanned passing trial (own task revision, valid cell digest, terminal PASS attempt) is rejected rather than ranked; per-field identity mismatches and a missing row are rejected; seven malformed frozen-matrix variants are rejected. Negative control: with the guard bypassed in-process (pre-fix row loading), the unplanned-trial test fails with `CampaignNotAggregatable not raised`, then passes with the guard — the regression demonstrably catches the leak.

## Corrections

- Hosted aggregation emits every persisted attempt, including infrastructure-invalid replacements. All attempt role costs enter campaign/verifier totals and all attempts enter attrition. Only the first valid scored attempt resolves a trial; later attempts cannot improve or double-count its score.
- Terminal validity uses an explicit verdict-status allowlist. Scorer errors, cancellations, infrastructure-invalid, missing, and unrecognized statuses are not execution-valid. Scoring also requires a matching evaluation verdict.
- Deadline observations default to unknown. Hosted persistence cannot establish a deadline flag, so hosted campaign and per-entrant deadline rates remain null. Mixed known/unknown populations remain unavailable rather than becoming zero or a rate over a selected subset; explicit false still produces a known zero.
- Role accounting returns null for missing requests, pending receipts, all-null costs, or partially unknown physical retries. Reported cost takes precedence over estimated cost, including reported zero. Known retry costs are summed with Decimal.
- Regenerated the ENG-014 typed client with pinned openapi-typescript 7.13.0. The OpenAPI schema itself was already current.

## Regression evidence

Real disposable PostgreSQL 18 on Windows, loopback port 5547, database `aieb_eng011_test`; migrated through Alembic head `e304c7d58a21`. No SQLite or mocked database substitute. No provider calls or benchmark executions.

Before the production fixes, targeted PostgreSQL tests reproduced lost replacement observations, invalid scorer-error classification, fabricated deadline false, and all-null receipt costs becoming zero. The original two aggregation tests still passed. A subtest cleanup issue was corrected with unconditional savepoint rollback.

Final completed runs:

| Check | Result |
| --- | --- |
| `python -m unittest tests.test_api_service.ApiServiceTests -v` | 65 passed, 53.113 seconds; includes eleven aggregation tests (three new membership regressions) |
| `python -m unittest tests.test_analysis.AnalysisTests -v` | 17 passed |
| `python -m unittest tests.test_accounting_and_cli -q` | 6 passed |
| `python scripts/generate_openapi.py --check` | matches current API |
| `python scripts/generate_typescript_client.py --check` | matches pinned generator output |
| `npm test -- --reporter=dot` in apps/web | 32 passed across 12 files |
| `npm run build` in apps/web | TypeScript and Vite production build passed |

The captured full API log is locally available at `.cache/eng011-full-api.stderr` (ignored, not a committed artifact). The executable regressions are in `tests/test_api_service.py` and `tests/test_analysis.py`.

Replacement regression: two scored attempts costing $6 each plus one invalid predecessor costing $60 produce $72 total, $36 verifier spend, 1/3 attrition, and $3 cost per resolution; replacement does not inflate scored n. A separate case proves unknown predecessor spend makes campaign/verifier totals unavailable without contaminating the known scored cost per resolution. Another proves an initial scored failure cannot be replaced by a later pass.

## Remaining boundaries

This does not implement Prompt 14 publication selection/semantic snapshot binding. No historical immutable publication is rewritten. Hosted deadline persistence remains unavailable rather than inferred from status. Separate infrastructure expense accounting and per-entrant planned counts remain existing limitations. Local artifact checks are green; no remote CI run is claimed.
