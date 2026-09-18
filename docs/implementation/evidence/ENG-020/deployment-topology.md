# ENG-020 — Deployment topology (spec section 40)

Written as configuration/documentation only. **Nothing here is deployed** - no cloud budget or
authorization exists in this environment (same disclosed constraint as ADR-12 and ENG-001's
real-agent gate).

## Components (spec section 40's P2 topology)

| Component | Image | Notes |
| --- | --- | --- |
| Web static assets + API | `deploy/docker/Dockerfile.api` | FastAPI (`aieb_api.app:app`), stateless; scales horizontally |
| PostgreSQL | managed (not self-hosted in production) | control-plane DB; managed backups/PITR per spec section 40 |
| Object store | not yet built (ADR-11: owed to ENG-019, re-deferred) | staging currently uses PostgreSQL (ADR-11), disclosed as an interim deviation |
| Execution workers | `deploy/docker/Dockerfile.worker` | `aieb_api.worker.loop`; drains on SIGTERM (ENG-020) |
| Verifier workers | same image, `work_type="verification"` claims | independently leased (ENG-015/ENG015-007); no privileged access beyond its own lease |

## Least-privilege credentials (spec section 40)

Worker identities must never be able to administer the API database. Concretely: the
`AIEB_DATABASE_URL` injected into worker deployments must use a Postgres role with INSERT/
UPDATE/SELECT on exactly the tables `worker/repository.py` touches (`work_item`, `attempt`,
`candidate`, `evaluation`, `worker_artifact_blob`, `worker_artifact_reference`, `campaign` for
read + the narrow auto-pause/kill-switch columns) - never `DROP`/`ALTER`/`TRUNCATE`, and never
the `publication`/`review`/`users`/`role_bindings` tables an API-only role would need. This role
separation is a deployment-time database-grants concern, not application code; it is documented
here as a requirement, not yet provisioned (no managed database exists to grant roles on).

## Environments (spec section 40)

- **local**: `AIEB_ENV=local`, developer's own disposable Postgres.
- **CI**: `AIEB_ENV=test`, ephemeral Postgres service container per workflow run (see
  `.github/workflows/eng015-verification.yml`, `sandbox-integration.yml`,
  `release-candidate.yml`). No production secrets or hidden fixtures ever enter PR-triggered CI
  - confirmed: none of the five workflows reference a production secret, and the fixture
  evaluators under `tests/maintainer/` are already excluded from any built image (they are test
  code, never copied into `deploy/docker/Dockerfile.*`).
- **staging**: dedicated keys/data, constrained spend - not provisioned.
- **production**: declarative configuration, secrets injected at deployment - not provisioned.

## Rollback (spec section 45)

Both Dockerfiles produce images tagged by immutable digest (a real registry push would tag by
git SHA or content digest, not `latest`) - rollback means pointing traffic back at the previous
compatible image, never deleting scored evidence to hide a bad outcome. Database migrations
follow expand-migrate-contract (verified for the latest boundary by
`scripts/migration_rollback_drill.py`'s leg 2 - old application code must tolerate a freshly
migrated schema during the rollout window). Active campaigns keep their pinned toolchain
(frozen `campaign.resolved` manifest, unaffected by a later entrant/task/evaluator revision
being registered) across a software upgrade - this is the existing ENG-002/ENG-014 frozen-
manifest design, not new to ENG-020; a software deploy never mutates an already-frozen
campaign's resolved cohort.

## Disaster recovery targets (spec section 40)

Metadata RPO <=15 minutes, RTO <=4 hours, "tested before official launch". Not yet tested
against real infrastructure (none exists). `scripts/backup_restore_drill.py` tests the
RECONCILIATION BEHAVIOR a restore must exhibit (see `operations-review.md`) against a real
`pg_dump`/`pg_restore` cycle on disposable databases; its reported restore duration is
explicitly disclosed as a local-proxy measurement, not a claim against the real RPO/RTO
targets, which require real managed backup infrastructure to measure honestly.
