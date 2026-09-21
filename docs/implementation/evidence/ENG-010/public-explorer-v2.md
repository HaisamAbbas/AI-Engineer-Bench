# Prompt 10 — public results explorer

Implemented a read-only frontend surface over published/read-only API routes.
The reachable route graph contains results, comparison, entrant profiles, task
catalog/detail, run evidence, methodology, releases, corrections, and local
documentation. Campaign creation, lifecycle controls, task upload, evaluator
changes, publication review, and sign-in controls were removed from the web
bundle and are not linked or routable.

The existing typed query hooks consume API snapshots; official values remain
server-provided and the client does not calculate rankings or scores. Existing
pages expose empty, loading, error, unavailable, incomplete, and development
status states, with compatibility checks delegated to the comparison API.

Verification:

- TypeScript `--noEmit --incremental false`: passed.
- Browser/unit Vitest could not start in this Windows workspace because Vite's
  dependency optimizer receives `spawn EPERM`; this is an environment failure,
  not reported as a passing browser check.
- No official publication or live campaign was created.

Remaining acceptance gates are fixture-backed API contract tests, accessibility
browser checks, snapshot-integrity tests, and independent review. The product
remains a read-only development explorer until those gates pass.
