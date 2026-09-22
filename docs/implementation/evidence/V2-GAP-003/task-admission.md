# V2-GAP-003 — Task admission state machine

Status: PARTIAL. This document records implementation evidence only; it does
not claim that a task has been officially admitted or independently reviewed.

## Implemented

- `services/api/src/aieb_api/admission.py` defines the pinned
  `aieb.admission-protocol/v1`, bounded executor report validation, mandatory
  gate classification, unique/contiguous repeated clean-reset evidence, immutable result
  digests, and independent-review transition.
- `services/api/src/aieb_api/routes/admissions.py` exposes private,
  authenticated start/execute/read/gates/review/cancel operations with
  idempotency keys on mutations.
- `6f2a9d5c1e73_task_admission_state_machine.py` persists admission state,
  runs, gates, resets, and reviews. PostgreSQL triggers protect state
  transitions, terminal-run identity, gate/reset append rules, and review
  independence/evidence binding.
- Frozen task revisions are initialized as `frozen`; freezing alone cannot
  make a revision release-eligible. Campaign registry resolution calls the
  admission eligibility check.

## Verification performed

```text
python -m py_compile <admission implementation files>  PASS
pytest tests/test_task_admission_state_machine.py -q  6 passed, 3 skipped
alembic heads  6f2a9d5c1e73 (head)
```

The skipped tests require `AIEB_DATABASE_URL` and a disposable PostgreSQL
instance. TypeScript client regeneration was not run because the pinned npm
package was unavailable in the offline cache.

The follow-up integrity review is addressed in the working tree: duplicate
`(matrix_case_id, reset_number)` entries are rejected during report parsing;
required reset numbers are `1..min_resets` per matrix case; release eligibility
checks the same per-case unique coverage; and execution/release paths reject a
protocol digest that does not match its pinned protocol version.

## Remaining acceptance gates

- Execute the migration upgrade/downgrade/upgrade drill against PostgreSQL.
- Run the three persistence tests and the full API/campaign regression suite.
- Execute a real configured admission executor against a pinned task package;
  fixture-only rows are not admission evidence.
- Record a genuine independent reviewer decision for any task intended for a
  release.
- Regenerate and verify the typed client artifact after network/package access
  is available.
