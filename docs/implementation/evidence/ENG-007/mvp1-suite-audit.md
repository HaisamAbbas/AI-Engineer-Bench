# Prompt 07 completion report — MVP-1 task suite audit

Date: 2026-09-21

## Implemented functionality and changed files

- Added `scripts/validate_mvp1_suite.py`, a fail-closed catalog audit for the
  three required application categories and depth indicators.
- Added `tests/test_mvp1_suite.py`.
- Added explicit `mvp1.yaml` tickets, public/hidden fixture references,
  regression requirements, operational edge cases, and shortcut controls for
  the nine previously undocumented variants. These declarations make their
  intended scope reviewable; they do not by themselves convert a shallow
  implementation into an admitted task.
- Relabeled every existing catalog entry from `validated` to
  `development-only`; the catalog remains `pending-independent-review`.

## Audit result

The catalog has three declared categories (RAG, extraction, tool workflow).
Each package now has a task-specific capability declaration and complete
maintainer-facing scope metadata, so the structural audit recognizes all
twelve as development candidates:

- `rag.document-freshness` — RAG anchor with an explicit ticket, indexing,
  retrieval, metadata, citation, freshness, hidden-case references, and
  shortcut controls.
- `ext.batch-alignment` — extraction anchor with schema, batching,
  malformed-item, partial-failure, and correspondence requirements.
- `tool.idempotent-write` — tool-workflow anchor with state, confirmation,
  idempotency, ambiguity, recovery, and duplicate-effect controls.
- The other nine variants each declare a focused capability set rather than
  pretending every task covers every family concern. Their implementations
  still require runtime admission and independent review.

All tasks remain development-only, and no official or independent-review claim
was made. Existing task packages do contain reference, alternative,
counterexample, environment, and maintainer evaluator material, but that alone
does not establish genuine application diversity or deep MVP-1 coverage.

## Tests/commands actually run and results

- `python -m unittest tests.test_mvp1_suite tests.test_campaign_protocol tests.test_task_admission tests.test_v2_contracts tests.test_dev_bootstrap -v` — 16 passed.
- `python scripts/validate_mvp1_suite.py` — 3 categories detected; official
  release eligibility false; shallow-or-blocked findings emitted.
- `git diff --check` — passed apart from existing line-ending warnings.

## Acceptance gates

- Satisfied: suite-level diversity audit, explicit development-only labels,
  synthetic/redistributable provenance boundary, and no silent official claim.
- Pending: execute the admission matrices/resets, independently review each
  task, and verify that the declared controls are exercised by clean runs.
- Blocked: private official holdouts and official campaign/publication remain
  authorization-gated.

## Development-only task list

All 12 catalog entries are structural candidates but remain development-only;
independent review, admission evidence, private holdouts, and official gates
are still pending. None is admitted for official use.

Static `admit_task.py` checks pass for all 12 packages (package shape,
license/provenance, public/private separation, and reference/alternative/
counterexample presence). Runtime matrices are not claimed for packages without
an explicit `runtime.json` admission entry.

## Exact next command or numbered prompt

- Prompt 08 — analysis and immutable release artifacts, after the MVP-1 suite
  receives genuine task content and admission evidence.
