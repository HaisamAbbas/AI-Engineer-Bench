# ENG-021: Campaign Execution Guide (Future)

## Overview

This document describes the campaign execution process that WILL be followed
once all prerequisite gates are satisfied. **Do not execute** until:

- ENG-001: Real installed-agent smoke is complete and authorized
- ENG-012: Real pilot execution is complete and authorized
- ENG-019: VM-level hardened isolation is complete and validated
- ENG-020: Staging/production deployment is live and verified
- Independent review: All 12 tasks pass independent admission review
- Python lint/type CI gate is implemented

## Campaign Specification

Per the frozen campaign proposal (`official-campaign-proposal.json`):

| Parameter | Value |
|-----------|-------|
| Suite release | development-preview-proposed |
| Track | agents (reference) + models |
| Dependency mode | fixture |
| Budget profile | cpu-standard-v1 |
| Protocol | official-protocol-v1 |
| Total tasks | 12 |
| Entrants | 3 (reference-agent, model-a, model-b) |
| Repetitions | 5 |
| Dependency modes | 1 |
| **Total trials** | **180** |
| Max replacements | 2 (total with repl: 540) |
| Interleaving | Cyclic permutation |

## Trial Matrix

12 tasks × 3 entrants × 5 reps × 1 mode = 180 trials.

Each cell is identified by: `task_id::entrant_id::dependency_mode::rN`

Ordering: For each repetition, cycle entrants across tasks (spec section 19).

## Execution Steps

1. **Verify prerequisites** — run `scripts/audit_prerequisites.py --output evidence.json`
2. **Build bundle** — `python scripts/build_release_bundle.py --output /out/bundle`
3. **Run tests** — `python -m unittest tests.test_eng021_bundle_exclusions -v`
4. **Validate tasks** — `aieb task validate --all` (uses `hash_evaluator_closure`)
5. **Execute campaign** — `aieb campaign run official-campaign-proposal.json`
6. **Monitor** — watch prometheus alerts (config-as-code in `deploy/alerts/`)
7. **Handle infra-invalid** — replace up to 2 times per cell (total 540 max)
8. **Aggregation** — results stored in `campaign` table, aggregated via `aggregate_campaign_snapshot`
9. **Publication** — two-human review → `PUBLICATION` table (immutable once written)
10. **Analysis** — report per-task Wilson intervals, suite rate, MME assessment

## Statistical Protocol

- **Per-task rate**: p_hat = s/n, Wilson 95% CI
- **Suite rate**: Unweighted mean across 12 tasks (spec section 28)
- **Clustering**: 3 base application types (exploratory, not population-wide)
- **MME**: 0.10 (10 percentage points), prespecified
- **Power note**: At 5 reps, MDD ≈ 0.62 >> MME 0.10. Campaign is NOT powered
  to detect the MME. Must re-evaluate repetition count after ENG-012 authorization
  and real pilot variance measurement.

## Cost Accounting

- **Per-role budgets**: engineer ($0.15), dev_app ($0.02), verifier_app ($0.02), verifier_judge ($0.01)
- **Environment upper bound**: $0.10 per trial
- **Total reservation (estimated)**: ~$39.12 (with 20% margin: ~$46.94)
- **Enforcement**: `estimated_time_limited` — NOT hard-enforced (no provider reservation integration)

## Infrastructure-Invalid Policy

- Valid failures: retained as scored FAIL (terminal_status='fail')
- Infra-invalid attempts: retained for attribution, NOT scored; replaced per max_replacements (2)
- Total campaign cost includes ALL attempts (valid + invalid)
- cost_per_resolution only counts scored observations (valid AND verdict IS NOT NULL)

**RETENTION CITATION**: Based on ENG-011's `execution_valid`/`scored` populations.
See `services/api/src/aieb_api/aggregation.py::summarize()`.

## Cancellation & Retry

- **max_replacements**: 2 (enforced in `repository.reconcile_expired_leases`)
- **Auto-pause**: 3 consecutive infra failures per entrant → campaign auto-paused
- **Resume**: requires explicit `acknowledge_auto_pause=true`
- **Global kill switch**: dispatch-wide, tears down all non-terminal campaigns
- **Manual cancel**: includes paused state in teardown

## Publication Safety

- **Immutability**: `PUBLICATION` table rows are frozen once written (migration c9a1e7d4b260)
- **Two-human approval**: REQUIRED — not substituted by any automation
- **Redaction**: Whitelist-based trace event filtering; public usage from immutable evaluation only
- **Corrections**: Append-only (status → 'superseded'), prior publication preserved
- **Withdrawal**: Append-only (status → 'withdrawn'), original record preserved
- **Snapshot integrity**: BEFORE UPDATE trigger + app-level recompute (returns 503 on mismatch)

## Incident Response

If a protected path (e.g., `tests/maintainer/`, holdout fixture) leaks into a
public bundle:

1. **Quarantine**: Stop the release process immediately
2. **Rotate**: Rotate any leaked evaluator secrets/keys
3. **Re-audit**: Re-run `tests.test_eng021_bundle_exclusions` to confirm fix
4. **Inspect**: Verify no real holdout data was exposed
5. **Resume**: Only after independent human review

See `docs/implementation/evidence/ENG-020/runbooks.md` for full procedures
(provider outage, spend, worker disappearance, scorer defect, fixture exposure,
incorrect score).
