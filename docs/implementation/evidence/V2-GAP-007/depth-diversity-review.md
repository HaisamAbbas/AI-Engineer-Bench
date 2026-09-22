# V2-GAP-007 depth and application-diversity audit

The development catalog is not admitted by this report. The audit is a
repeatable pre-screen that prevents metadata and a shared HTTP harness from
being mistaken for deep application projects.

Run from the repository root:

```text
python scripts/validate_mvp1_suite.py \
  --output docs/implementation/evidence/V2-GAP-007/mvp1-depth-audit.json
```

Audit schema v2 excludes generic servers, tests, fixtures, migrations,
generated files, vendored code, references, alternatives, and counterexamples.
It counts statement start lines rather than multiline spans, and records
authored source modules, symbols, branch points, normalized AST fingerprints,
explicit application-project identity, engineering mechanism, and change
surface. Current floors require 12-20 curated tasks, at least two authored
domain modules, 40 statement lines, four symbols, and four branch points per
task. A project requires at least three curated tasks, 180 aggregate statement
lines, two deep tasks, and unique engineering mechanisms. These are rejection
signals, not a semantic quality score.

Current result from the checked-in legacy twelve-task catalog:

- all three categories are represented by development fixtures, but category
  labels no longer count as curated diversity;
- all twelve thin fixtures have an explicit `reject` curation decision and a
  task-specific reason in `suites/dev/catalog.json`;
- zero tasks are curated candidates and every project fails closed;
- `depth_diversity_satisfied` is `false` and the catalog remains
  development-only.

The release bundle builder excludes rejected tasks by default and refuses to
create an empty candidate bundle. `--include-rejected-development` exists only
for a clearly labeled local fixture bundle. `--require-admitted-suite` refuses
before creating output while structural, diversity, admission, or independent
review gates are incomplete. Passing this screen would not itself admit a task:
clean admission/reset evidence, holdout boundaries, and independent human
reviews remain mandatory.
