# Development suite

`catalog.json` retains twelve local fixture tasks for development and regression
controls. Under `aieb.track-a-curation/v2`, every one is explicitly
`rejected-shallow`: these compact synthetic backends are useful harness inputs,
but they are not counted as deep application tasks or release candidates.
Each rejection has a task-specific reason. Rejected fixtures are excluded from
release bundles unless the caller uses the clearly non-release
`--include-rejected-development` mode.

No curated Track-A candidate or admitted-only suite currently exists. New or
substantially rebuilt tasks must pass the structural rejection screen, runtime
admission matrix, reset requirements, holdout boundary, and independent human
review before the catalog can label them admitted.

The ENG-001 disposable contract fixture lives under `tests/fixtures/` and is deliberately not a
benchmark task, reference solution, or score.
