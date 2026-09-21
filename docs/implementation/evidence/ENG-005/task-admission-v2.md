# Prompt 05 completion report — task authoring and admission

Date: 2026-09-21

## Implemented functionality and changed files

- Added `scripts/admit_task.py`, a maintainer-only package/admission checker.
  It requires instructions, starter repository, contracts, environment,
  reference, alternative, counterexamples, development tests, provenance,
  evaluator digest, and private fixture reference.
- It rejects customer/private material copied into the public package and
  reports automated evidence separately from `independent_review: pending`.
- Added `aieb task admit` to `packages/aieb-cli/src/aieb_cli/main.py`.
- Added `tests/test_task_admission.py` covering package completeness and the
  no-self-certification rule.

Existing `aieb task validate` remains responsible for canonical TaskRevision
and real on-disk digest checks. Existing per-task admission scripts remain the
execution layer for baseline/reference/alternative/control matrices; the new
checker can invoke a task runtime matrix with `scripts/admit_task.py --execute`.

## Tests/commands actually run and results

- `python -m unittest tests.test_task_admission tests.test_v2_contracts tests.test_dev_bootstrap -v` — 10 passed.
- `python scripts/admit_task.py suites/dev/rag.document-freshness` — accepted
  package shape, development-only status, and pending independent review.
- Python compilation and `git diff --check` — passed (existing line-ending
  warning only).

## Acceptance gates

- Satisfied: strict package-shape checks, digest/evaluator-reference presence,
  license/provenance checks, private-fixture separation, baseline/reference/
  alternative/control entrypoint support, and explicit development-only status.
- Pending: running every task's full admission matrix, clean-reset evidence
  across all tasks, and independent human review metadata.
- Blocked: official admission/publication and private holdout use remain
  authorization-gated; no official result is claimed.

## Decisions or specification discrepancies

- Automated evaluator review and admission evidence are intentionally not
  treated as independent human approval.
- Existing development tasks were not bulk-created or relabeled as official.

## Exact next command or numbered prompt

- Prompt 06 — implement the campaign and reliability protocol.
