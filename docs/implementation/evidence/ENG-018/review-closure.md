# Prompt 14 — nine-point review closure

Verified locally on 2026-09-18 (Windows local date), uncommitted working tree.
This supplements, rather than replaces, the historical results in
[prompt14-verification.md](prompt14-verification.md).

## Review points

1. **Mutation replay / identity:** all 12 persisting API operations use
   principal-scoped replay records. The OpenAPI audit excludes only the
   non-persisting matrix-preview POST. Business writes and replay response share
   a transaction. Browser handles use `/v1/me` issuer/user ID rather than token
   bytes; storage failure aborts before sending. Hook tests cover all 12 writes,
   principal changes, token renewal, identity failure, and storage failure.
2. **Publication eligibility:** ranked preparation/approval requires
   `complete_for_rank`. A protocol declaring `required_trace_coverage` is checked
   for missing engineering/verification phase-start events, but even when both
   are present, publication is unconditionally rejected: `phase.started` markers
   only prove lifecycle presence, not complete model/application/action trace
   instrumentation, and no trusted, versioned trace-completeness contract exists
   yet (see `services/api/src/aieb_api/routes/publications.py`, the
   `required_trace_coverage` branch of `_publication_eligibility_error`).
   This is a deliberate fail-closed disclosed limitation, not a passing gate —
   no such protocol can currently be published, ranked or not. Default release
   selection excludes non-ranked publications; explicit historical reads remain
   available. Focused regressions cover these cases.
3. **Campaign completion:** bounded `SKIP LOCKED` sweep, locked fresh-state reads,
   paused completion, in-transaction frozen zero-work cancellation, and terminal
   reservation settlement. PostgreSQL tests cover rollback before replay commit,
   concurrent disjoint sweepers, locked rows, stale identity-map state, repeated
   settlement, and a single winner for concurrent cancellation completion.
4. **Budget compatibility:** v1 round-trip preserves its schema/digest; v2 includes
   the environment bound. Estimate = trials × (role caps + environment bound) ×
   (1 + replacements); unknown inputs produce null, not zero. Three focused tests
   pass alongside API reservation tests.
5. **Review trust:** server-derived, signed review kind; independence requires the
   attestation; creator/preparer self-approval is blocked. API regressions pass.
6. **Provenance / reasons:** separate correction and append-only withdrawal
   reasons. Migration `b7e4a9c2d1f8` freezes signed provenance; additive migration
   `c9a1e7d4b260` also freezes publication identity, campaign, class, timestamp,
   and terminal status. Direct SQL mutation regressions pass. Valid supersession
   remains supported; the identity migration does not impose a new reason
   requirement on legacy direct-SQL status changes.
7. **Corrections UI:** displays both reasons, publication class, and before/after
   publication IDs. Removed the stale “reasons not recorded” text; tests pass.
8. **Browser authentication:** `/v1/me`, authorization-code + S256 PKCE, no SPA
   secret, session-only token storage, no manual token input. Navigation uses
   server roles. Real-browser viewer/operator checks pass.
9. **Browser → HTTP acceptance:** repeatable harness added at
   `scripts/check_admin_browser.py` and `apps/web/scripts/admin-browser-check.mjs`.
   Real Edge, Vite, HTTP FastAPI, and PostgreSQL; local HTTPS fixture IdP with
   one-use codes and verified PKCE. The API uses its test-only HS256 verifier;
   this is NOT acceptance against a deployed OIDC/JWKS provider.

## Actual verification results

- Initial uninterrupted backend discovery: **217 run, 214 passed, 2 errors,
  1 optional Harbor skip**, 436.402 seconds. Both errors were a new status
  trigger incorrectly blocking existing direct-SQL withdrawal / supersession.
  The restriction was corrected; neither original failing test was weakened.
- Final targeted backend batch: **110 tests passed**, 216.914 seconds, exit 0:
  `tests.test_api_service`, `tests.test_api_migrations`,
  `tests.test_api_idempotency_audit`, `tests.test_review_closure`,
  `tests.test_budget_compatibility`, `tests.test_campaign_start_atomicity`.
  This includes the full API module and both originally failing tests.
  **No claim of a subsequent all-green full-discovery run.**
- Full frontend: **88 tests / 19 files passed**, 33.52 seconds; production build
  and TypeScript checks passed. OpenAPI and pinned TypeScript staleness checks
  passed. `git diff --check` passed (line-ending warnings only).
- Browser harness passed both logins, role navigation, create, discarded successful
  response, page reload, same-key retry, edit, two-trial preview, freeze, start.
  Both POST responses were 201 with the same campaign ID and idempotency key.
  Independent DB assertions found exactly **one campaign and one reservation**,
  campaign state `running`. No worker was started and no benchmark was executed.
  Persistent localStorage was empty; PKCE verifier was consumed.

## Reproduce browser acceptance

Use a **new empty disposable PostgreSQL database**, not one used concurrently by
backend tests. Install Python/project dependencies and `apps/web` npm dependencies.
From the repository root (PowerShell):

```powershell
$env:AIEB_DATABASE_URL = '<PostgreSQL SQLAlchemy URL for a new disposable database>'
.venv/Scripts/python.exe scripts/check_admin_browser.py
```

Requires Node 22+ and installed Edge/Chrome; optionally set
`AIEB_BROWSER_EXECUTABLE`. Fixed loopback ports: API 8026, Vite 5176, fixture IdP
8446 (must be free). The harness migrates/seeds the empty DB, starts/stops services,
then removes ephemeral fixture tokens and TLS keys. It refuses a nonempty DB.
It leaves staging records for inspection. The browser trusts only the ephemeral
fixture certificate's public-key pin, not arbitrary certificates.

## Local evidence (cache artifacts, not committed)

| Artifact | SHA-256 |
| --- | --- |
| `.cache/review-api-final.log` | `d35d67185e5903e6cb00c77dcbe1bc848f3edb1e62527d739e6152d9e68e4b1e` |
| `.cache/review-discovery.log` | `6e6130356a85959deef6b7a824269004c59e88ccc3b38aeaab6d98bb271d92ff` |
| `.cache/review-web-final.log` | `d26e9736364321f606b11e5e27dc822071cf8d16f94c40cc3db3aa32b27bf300` |
| `.cache/admin-browser/report.json` | `9497504e496bcb69ec41a0b06686a7487396f847f6888dcd111f7ac414f0346f` |

Browser screenshot: `.cache/admin-browser/running.png`. API/Vite logs are in the
same directory. Evidence contains no bearer tokens.

## Remaining external acceptance

Current-tree remote CI, real deployed OIDC/JWKS login, independent review-console
acceptance, ENG-011 human-review gates, and opt-in Harbor/Docker compatibility are
not established by these local checks. No deployment or public release occurred.
ENG-017/018 are not promoted to COMPLETE solely by this review.
