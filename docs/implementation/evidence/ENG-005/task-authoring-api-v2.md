# Maintainer task-authoring API

Added private `POST /v1/maintainer/task-drafts`, `PATCH /v1/maintainer/task-drafts/{id}`,
and `POST /v1/maintainer/task-drafts/{id}/freeze`. It requires an operator,
reviewer, or administrator role and an `Idempotency-Key`. The endpoint:

- validates the versioned task manifest and ticket text;
- persists a mutable draft first, then freezes it into an immutable revision;
- creates or reuses an evaluator revision at freeze;
- binds the frozen task revision to the manifest and ticket digest;
- returns a development draft or frozen revision with independent-review status pending;
- never executes a model, runs admission, freezes a campaign, or publishes.

Duplicate slug/version revisions are rejected. Idempotent retries replay the
original response. Runtime admission and official release remain separate
operator workflows.

The authored adapter is `POST /v1/maintainer/task-drafts/authored`. It requires
repository URL, immutable source revision, content digest, license, and
provenance digest, and records `source_strategy=authored`. Mined-PR and
live-window sources are intentionally deferred to separate adapters and cohort
rules.

The mined-PR adapter is `POST /v1/maintainer/task-drafts/mined-pr`. It requires
the repository URL, base and patch commits, pull-request identity, license and
provenance digests, contamination cutoff, and an explicit public/held-out/
restricted split. The PR metadata is retained for review; registration alone
does not admit or publish the task.

The live-window adapter is `POST /v1/maintainer/task-drafts/live-window`. It
requires a dedicated `mvp2-live-*` cohort, pinned snapshot revision and
content digest, collection/release timestamps, model cutoff, and expiry. It
rejects timezone-less or out-of-order windows and does not treat an expired
snapshot as current official evidence.

Verification: task-authoring route/schema tests pass. PostgreSQL transaction
and idempotency integration coverage remains required before production use.
