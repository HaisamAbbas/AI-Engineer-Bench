# V2-GAP-005 — Private holdout registry

Status: PARTIAL / BLOCKED. This is implementation evidence, not proof that an
official private corpus exists.

Implemented:

- `holdout_manifest` stores only opaque private-provider URIs, object digests,
  lengths, family/evaluator identity, split/retention policy, protocol/access
  scope, and a canonical manifest digest.
- Frozen manifest identity is protected by a PostgreSQL trigger; retirement is
  the only post-freeze status change.
- `holdout_review` is append-only and requires explicit overlap, storage-policy,
  evaluator-isolation, and manifest-integrity decisions. Authors cannot approve
  their own holdout and at least two distinct reviewers must approve.
- Freeze evaluates only the newest decision for each kind; a later reject or
  inconclusive decision invalidates an earlier approval.
- Freeze requires approved reviews of all four kinds in both the service and
  database trigger. The frozen digest is recomputed after the overlap evidence
  is attached, so the digest binds the final review identity.
- Database triggers bind family/evaluator identity to `task_revision` and reject
  author self-review or author self-freeze; frozen access repeats those checks.
- The same latest-decision and identity checks are enforced in PostgreSQL, not
  only in the HTTP service, so direct SQL cannot resurrect an older approval or
  forge task/evaluator provenance.
- `holdout_access_audit` is append-only and records actor, operation, scope,
  campaign/attempt, object digest, success, and request correlation without
  persisting fixture bytes.
- Private maintainer endpoints create, inspect, review, freeze, and retire
  manifests. Every mutating endpoint requires an `Idempotency-Key`; no public
  route serves holdout content or credentials.
- `holdout_storage.py` provides a capability-scoped, digest/length-verifying
  development directory adapter. It is disabled unless explicitly configured;
  S3/GS references fail closed until an approved deployment adapter exists.
  The directory adapter rejects traversal, symlinked objects, digest/length
  mismatches, and roots that do not contain the resolved object.
- Tasks explicitly referring to `holdout://<digest>` are release-ineligible
  unless a matching frozen holdout manifest exists.

Verification:

```text
tests/test_holdout_registry.py + test_release_reviews.py  13 passed, 2
skipped on Windows (symlink creation is unavailable on the current host)
python -m py_compile holdout implementation and migration  PASS
OpenAPI generation/check  64 paths, PASS
alembic heads  g1b2c3d4e5f6 (head; additive holdout review race guard)
```

Still required before closure:

- configure a real private object store and IAM policy;
- upload/curate an actual holdout corpus outside the repository/build context;
- run overlap and contamination review against recorded corpus digests;
- demonstrate evaluator-only scoped access and exported access logs;
- record independent human approval;
- run migration and PostgreSQL integration tests against the deployed schema.

No synthetic or local fixture is claimed as an official holdout.

## Review follow-up

Release eligibility now selects the exact `holdout://<digest>` object and
invokes the full `require_frozen()` gate before applying official split and
retention checks. Failed provider reads persist their audit event through an
independent database transaction, preserving the original storage error if
the audit transaction is unavailable. Holdout review/freeze/retire paths lock
the manifest row; migration `g1b2c3d4e5f6` also locks the parent in the review
trigger to close the concurrent-review race.

The adapter inspects every path component for symlinks/junctions and rejects
drive-qualified and traversal keys. Idempotency headers are trimmed and
bounded to 128 characters, and the new `e8f9a0b1c2d3` constraints are mirrored
in ORM metadata. The real private provider, corpus, evaluator access logs,
independent human approval, and PostgreSQL migration run remain external
gates, so V2-GAP-005 stays PARTIAL/BLOCKED.
