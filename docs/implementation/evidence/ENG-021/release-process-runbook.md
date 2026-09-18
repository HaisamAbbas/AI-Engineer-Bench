# ENG-021: Release Process Runbook

## Overview

This document describes the release process preparation for AI-Engineer-Bench's
official benchmark release. **Status: IN_PROGRESS** — the process has been prepared
but NOT executed. Independent human review is pending for all 12 tasks.

## Release Lifecycle

Per the catalog's §26 lifecycle:

```
DRAFT → VALIDATED → REVIEWED → PILOTED → RELEASED
```

Current state: All 12 tasks are at **VALIDATED**. None are at **REVIEWED** or
**RELEASED**.

## Three-Label Discipline

Per the prompt constraints and §22:

| Label | Meaning | Used For |
|-------|---------|----------|
| `development` | Local scratch/development | Never used for official purposes |
| `official-public-origin` | Tasks whose private examples are derived from public tasks | The existing 12 public tasks |
| `official-held-out` | Distinct held-out family (distinct apps) | **OUT OF SCOPE** — not yet created |

**Critical**: The 12 public tasks with private examples are labeled
`official-public-origin`, NOT `official-held-out`. Per §22, private examples on
public tasks do NOT constitute a contamination-free hidden benchmark.

## Release Candidate Preparation

Per DECISIONS.md ENG013-004: "Do not create an admission ledger or release
manifest ahead of independent review."

Instead, we produce a **candidate proposal**:

```
examples/proposed-release-candidate.json
```

This JSON carries:
- Per-task review status (`pending-independent-review`)
- Block gates (ENG-001, ENG-012, ENG-019, ENG-020, independent-review)
- Final statuses (ENG-021 → IN_PROGRESS, ENG-022 → BLOCKED)

## Build Script

To build a release candidate bundle (excluding protected paths):

```bash
python scripts/build_release_bundle.py --output /tmp/aieb-bundle-output
```

The bundle:
- Contains all 12 public tasks with verified digests
- **Excludes** `tests/maintainer/`, `dev_tests/`, and any holdout paths
- Includes `MANIFEST.json` with per-task digest verification

Run the exclusion test:

```bash
python -m unittest tests.test_eng021_bundle_exclusions -v
```

## Campaign Execution Guide

Campaign execution requires (all BLOCKED currently):

1. **ENG-001**: Real installed-agent smoke (provider/model authorization)
2. **ENG-012**: Real pilot execution (authorized provider/model config)
3. **ENG-019**: Hardened VM-level isolation (official VM provider)
4. **ENG-020**: Staging/production deployment (cloud authorization)
5. **Independent review**: All 12 tasks must pass independent admission review

The campaign proposal (frozen, 180 trials = 12 tasks × 3 entrants × 5 reps)
is at:

```
examples/official-campaign-proposal.json
```

## Cost Reservation

- **Enforcement**: `estimated_time_limited` (not hard-enforced)
- **No provider reservation integration** exists
- Assumptions-based estimates only
- Grand total reservation: ~\$39.12 (with 20% margin: ~\$46.94)
- **Status**: Estimates only, not enforceable

## Two-Human Approval

Per ENG-018: The `publication` table is immutable once published. The final
release must require two-human approval via the operational process. This is
NOT substituted by any automation in this preparation.

## Incident Response

If a protected path (e.g., `tests/maintainer/`, holdout fixture) leaks into a
public bundle:

1. **Quarantine**: Immediately stop the release process
2. **Rotate**: Rotate any leaked evaluator secrets/keys
3. **Re-audit**: Re-run `tests.test_eng021_bundle_exclusions` to confirm fix
4. **Inspect**: Verify no real holdout data was exposed
5. **Resume**: Only after independent human review

See the runbook for provider outage, spend, worker disappearance, scorer
defect, fixture exposure, and incorrect score procedures in
`docs/implementation/evidence/ENG-020/runbooks.md`.
