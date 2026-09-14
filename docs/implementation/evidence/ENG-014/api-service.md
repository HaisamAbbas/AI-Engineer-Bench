# ENG-014 evidence: hosted metadata, API and authentication

Date: 2026-09-14. Local development/test evidence only; no remote deployment occurred.

## What this ticket implements

- PostgreSQL persistence for `users`, `role_bindings`, `task_revision`, `evaluator_revision`,
  `fixture_revision`, `suite_release`, `suite_task`, `entrant_revision`, `campaign`, `trial`,
  `attempt`, `work_item`, `candidate`, `evaluation`, `artifact`, `artifact_ref`, `usage_request`,
  `usage_receipt`, `publication`, `review`, `audit_event`, plus `idempotency_record` for API-01.
  Uniqueness, foreign keys, check constraints, and nullable accounting columns are enforced by
  the database schema itself, not only in application code.
- Two Alembic migrations demonstrating the expand phase of expand-migrate-contract: the initial
  schema, then an additive nullable `campaign.submitter_note` column.
- FastAPI endpoints for registry/results reads and campaign draft/freeze only (the scope this
  ticket names); campaign start/pause/resume/cancel and review/publication writes remain
  ENG-017/ENG-018:
  - `GET /v1/tasks/{slug}/revisions/{version}` (public)
  - `GET /v1/entrants/{id}` (public)
  - `GET /v1/releases` (public, cursor-paginated)
  - `GET /v1/publications/{id}/results` (public; withdrawn snapshots remain addressable with a notice)
  - `GET /v1/comparisons` (public; 2-4 entrant_ids)
  - `GET /v1/trials/{id}` (authenticated)
  - `GET /v1/artifacts/{ref}/download` (authenticated; reference-scoped, 404s a private ref the caller does not own)
  - `POST /v1/campaigns` (operator; requires Idempotency-Key)
  - `PATCH /v1/campaigns/{id}` (operator; requires If-Match; 412 on stale revision; 409 once frozen)
  - `POST /v1/campaigns/{id}/freeze` (operator; requires Idempotency-Key; resolves the draft
    through `aieb_core.planner.freeze_campaign`, the same pure planner the local CLI uses)
- OIDC-based identity (`services/api/src/aieb_api/auth.py`): a `JWKSIdentityProvider` verifies
  RS256 tokens against a real issuer/JWKS/audience. When none of `AIEB_OIDC_ISSUER`,
  `AIEB_OIDC_JWKS_URL`, `AIEB_OIDC_AUDIENCE` are set, the service has no configured provider and
  every authenticated route returns 401 rather than granting access — it fails closed, it does
  not default to open. `TestIdentityProvider` (HS256 shared secret) raises `RuntimeError` unless
  `AIEB_ENV=test`, so it cannot be wired into a production process by accident.
- Typed errors, idempotency keys, optimistic concurrency (If-Match/revision), cursor pagination
  (default 50, max 200), and public/private response projections, matching spec section 33.

## What this ticket does not implement (tracked, not silently skipped)

- Campaign start/pause/resume/cancel, reviews, and publication-writing endpoints: ENG-017/ENG-018.
- PostgreSQL-backed worker leasing/reconciliation (`work_item` rows exist as a table; the leasing
  protocol itself is ENG-015).
- Real object storage / signed download URLs: `GET /artifacts/{ref}/download` returns artifact
  metadata and an authorization decision, not a byte stream — that requires ENG-015's storage work.
- A TypeScript client generated from the OpenAPI schema: deferred until `apps/web` exists
  (ENG-016); see DECISIONS.md ENG014-004. `scripts/generate_openapi.py` regenerates
  `docs/implementation/evidence/ENG-014/openapi.json` reproducibly today.
- Cohort/ProtocolRevision/BudgetProfile persistence: spec section 30's table list does not name
  separate hosted tables for these, so `POST /v1/campaigns/{id}/freeze` accepts them in the
  request body (mirroring the local CLI's registry) rather than inventing an unlisted table.

## Environment variables (no secrets committed)

| Variable | Purpose |
| --- | --- |
| `AIEB_DATABASE_URL` | SQLAlchemy connection URL, e.g. `postgresql+psycopg://user:pass@host:5432/db` |
| `AIEB_OIDC_ISSUER` | Real OIDC issuer URL; required (with the two below) for production auth |
| `AIEB_OIDC_JWKS_URL` | JWKS endpoint for RS256 verification |
| `AIEB_OIDC_AUDIENCE` | Expected `aud` claim |
| `AIEB_ENV` | Set to `test` only in isolated test processes; gates `TestIdentityProvider` |
| `AIEB_TEST_SHARED_SECRET` | HS256 shared secret; only read when `AIEB_ENV=test` |
| `AIEB_API_HOST`, `AIEB_API_PORT` | `aieb-api` entrypoint bind address (defaults `127.0.0.1:8000`) |

## Reproducing locally

```powershell
docker run -d --name aieb-test-postgres -e POSTGRES_PASSWORD=aieb_test_password -e POSTGRES_DB=aieb_test -p 5544:5432 postgres:16
$env:AIEB_DATABASE_URL = "postgresql+psycopg://postgres:aieb_test_password@localhost:5544/aieb_test"
cd services/api
../../.venv/Scripts/python.exe -m alembic upgrade head
$env:AIEB_ENV = "test"
.venv/Scripts/python.exe -m unittest tests.test_api_auth tests.test_api_service -v
```

## Test evidence (real PostgreSQL, not sqlite)

`tests/test_api_service.py` runs against the disposable container above (skipped, not faked,
when `AIEB_DATABASE_URL` is unset) and covers: API-01 (idempotent replay and conflicting-key
reuse), API-02 (private artifact ref denied as 404 without leakage, matching the shape of a
missing ref), stale If-Match edits (412), invalid state transitions (freezing an already-frozen
campaign, editing a frozen campaign), corrupt manifests (an unresolvable task reference is
rejected, not silently dropped), and public registry reads requiring no auth. `tests/test_api_auth.py`
covers fail-closed behavior with no database required. Migration compatibility (upgrade →
downgrade → upgrade) was run against the same real instance; see command history in this file's
git history for the exact commands.

## Post-review fixes (2026-09-14)

An independent review found six real defects, all fixed and covered by new tests:

- **Concurrency** (`PATCH .../campaigns/{id}`, `.../freeze`): read-then-write optimistic
  concurrency was a TOCTOU race under real concurrent requests. Replaced with atomic
  `UPDATE ... WHERE id=... AND state=... AND revision=...` statements gated by `rowcount`.
  `idempotency.check_or_reserve`'s check-then-insert had the same race, fixed by
  `idempotency.finalize`, which commits business-logic writes and the idempotency record
  together and reconciles a unique-constraint conflict as a replay-or-409 instead of an
  unhandled `IntegrityError`. All three races are exercised with real `threading.Thread`
  concurrency against the real test Postgres instance in `tests/test_api_service.py`
  (`test_concurrent_*`), not simulated sequentially.
- **Access control** (`GET /v1/trials/{id}`): required only a valid token, not a role, unlike
  the neighboring artifact-download endpoint. Now gated to operator/reviewer/administrator.
- **Fail-closed gaps** (`auth.py`): a token missing `sub` raised an unhandled `KeyError` instead
  of the promised 401. Fixed in both identity providers.
- **Corrupt data** (`registry.py`): a stored manifest failing its own pydantic contract raised
  an unhandled `ValidationError` on read. Now returns 503 `service_unavailable`, distinct from
  404 (the record exists) and from a client error (retrying will not fix corrupt server data).

See DECISIONS.md ENG014-005/006 for the reasoning kept for each fix.

## Second-pass review fixes (2026-09-14)

A follow-up review of the first fix pass found it incomplete rather than wrong: three real
gaps, plus three lower-severity items worth hardening. All fixed, all covered by tests using
real thread concurrency or direct fault injection rather than inspection alone:

- The corrupt-manifest guard reached `registry.py` but not the equivalent
  `TaskRevision`/`EntrantRevision.model_validate` calls inside `freeze()`. Extracted
  `revisions.validate_stored_manifest` and used it at both sites.
- Two concurrent `freeze()` calls sharing the same Idempotency-Key both pass
  `check_or_reserve` before either commits; the atomic-UPDATE loser previously fell straight
  into `conflict()` (409) instead of being recognized as a legitimate retry. The zero-rowcount
  branch now re-checks the idempotency table before concluding a real conflict.
- `freeze()`'s atomic guard checked `state='draft'` but not `revision`, so a PATCH committing
  between freeze's read and its write would be silently discarded. The guard now captures and
  checks `revision` too, mirroring `patch_campaign`.
- `session.get()` after a raw Core `UPDATE` relied on SQLAlchemy's `synchronize_session`
  behavior to stay fresh. Both `patch_campaign` and `freeze` now use `.returning(CampaignRow)`
  to get the authoritative row straight from Postgres.
- `idempotency.finalize`'s `except IntegrityError` assumed the cause was always the
  idempotency unique constraint; it now checks `exc.orig.diag.constraint_name` and re-raises
  anything else instead of crashing with an unhandled `NoResultFound`.

See DECISIONS.md ENG014-007.

## Handoff

Campaign draft/freeze endpoints are implemented and tested; campaign
start/pause/resume/cancel, reviews, and publication creation are the next hosted-API dependency
(ENG-017/ENG-018). Worker leasing against `work_item`/`attempt` rows (ENG-015) has no
implementation yet beyond the persisted schema. Local CLI functionality is unaffected — this
ticket added `services/api` as a new workspace member and touched no `packages/aieb-*` code.
