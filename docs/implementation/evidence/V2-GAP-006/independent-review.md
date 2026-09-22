# V2-GAP-006 — independent admission and release review

## Implementation status

The software gate is implemented, but this does not create a human review or
close the product gap by itself. Task admission already persists an
append-only `task_admission_review` row containing reviewer identity, scope,
decision, timestamp, the exact admission-result digest, an explicit
independence declaration, and a reason. Release/campaign approvals now use
the new `independent_review` table with the same first-class fields rather
than an unstructured JSON evidence blob.

The API derives the subject identity and evidence digest from the immutable
campaign or publication-preparation row. It rejects missing/invalid digests,
blank scope or reason, missing independence declarations, self-review, and
reviews whose evidence no longer matches the frozen target. PostgreSQL
constraints and a trigger enforce append-only rows, lowercase SHA-256
digests, reviewer/subject separation, and target-subject identity for direct
database writes. Campaign start and publication eligibility re-check the
recorded independent approval and digest; a forged state transition cannot
skip the review record.

## Verification performed

- Python compilation and `git diff --check` pass.
- Operator CLI, Harbor dispatch, and admission unit suites pass in this
  environment; PostgreSQL-backed API/migration tests are skipped because
  `AIEB_DATABASE_URL` is not configured.
- OpenAPI and generated web client artifacts were regenerated.

## Remaining acceptance gate

V2-GAP-006 remains **BLOCKED** until an actually independent human reviewer
records an approval for every admitted task revision and every release. The
repository intentionally contains no fabricated reviewer identity, timestamp,
decision, digest, declaration, or reason. Those records must be created via
the authenticated review workflow and then independently checked before a
release can be called official.

## Review follow-up

Additive migration `f0a1b2c3d4e5` adds the initial database triggers, and
`h1a2b3c4d5e6` hardens them further: unknown task authors cannot be reviewed
or admitted, publication reviews lock and check both the preparer and campaign
creator for approve and reject decisions, and target rows are locked before
state validation. New reviews retain the exact global reviewer role-binding
identity; referenced grants cannot later be edited or deleted, and review
timestamps are assigned from the database clock. Fixture admission now
provisions its reviewer role rather than weakening these checks. The Alembic
chain has one head at `h1a2b3c4d5e6`.

The focused non-PostgreSQL suites pass; PostgreSQL migration, direct-SQL, and
two-transaction concurrency regressions still require `AIEB_DATABASE_URL`.

These are enforcement improvements only. No human approval was generated, so
V2-GAP-006 remains **BLOCKED**.
