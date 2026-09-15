# ENG-016 evidence: public website

`apps/web` is a React 19 / TypeScript / Vite single-page app implementing Prompt 13's read-only
public routes: `/`, `/results`, `/compare`, `/entrants/:slug`, `/tasks`, `/tasks/:slug/:version`,
`/runs/:trialId`, `/methodology`, `/releases`, `/releases/:publicationId`, `/corrections`, `/docs`.
`/admin/*` is explicitly out of scope (ENG-017/018, Prompt 14).

Date: 2026-09-15, revised 2026-09-16 after a second independent review found six real gaps in the
first pass, all fixed here (see DECISIONS.md ENG016-003 through ENG016-006).

## CORS (the first-pass website could not actually be called from a browser)

`services/api` had no CORS middleware at all - reproduced directly: `OPTIONS /v1/releases` with an
`Origin` header returned no `Access-Control-Allow-Origin`, meaning a real browser's own preflight
blocked every request before it reached a route (curl/TestClient calls without an `Origin` header
never exercised this). Fixed with an explicit, configurable allowlist
(`AIEB_CORS_ALLOWED_ORIGINS`, defaulting to `apps/web`'s local dev origins) - not a blanket
allow-everything. `tests/test_api_cors.py` asserts the allowed origin gets the header on both
preflight and the actual request, and an unlisted origin gets neither.

## API integration - typed end to end, not `dict`

Every route the website calls now has a real Pydantic `response_model` (`services/api/src/aieb_api/
schemas.py`): `AnalysisSnapshot`/`TaskCellStats` mirror `aieb_analysis.metrics.summarize()`'s exact
output shape, `PublicationResultsResponse`, `ComparisonResponse`, and a generic `Page[ItemT]`
replacing the previous `items: list[dict]`. `GET /v1/publications/{id}/results` and
`GET /v1/comparisons` previously returned bare `dict`, so the frontend had to hand-write interfaces
and `as unknown as X` casts that would have silently kept compiling against a stale shape - they are
now real generated types (`src/api/types.ts` aliases onto `components["schemas"]`), and a schema
change now surfaces as a TypeScript build error instead.

Fixing this surfaced a genuine, separate bug: `_verified_snapshot` hashed the stored snapshot with
`aieb_core.canonical.content_hash`, which categorically forbids floats (a deliberate rule for
benchmark-identity content like task/campaign digests) - but `aieb_analysis` output legitimately
contains floats (rates, costs), so any real snapshot would have permanently failed its own integrity
check. Fixed with a dedicated `services/api/src/aieb_api/snapshots.py::snapshot_digest` (standard
canonical JSON, no float ban) used both when a snapshot is written and when it is re-verified on
read - see DECISIONS.md ENG016-004.

Six read/list endpoints exist because the website genuinely needs them:

- `GET /v1/tasks` - thin public task-catalog projection, filterable by category. No retired/
  deprecated status column exists yet (disclosed gap, unchanged).
- `GET /v1/corrections` - publications that supersede an earlier one or were withdrawn (no
  free-text reason column yet - disclosed, unchanged).
- `GET /v1/entrants/by-slug/{slug}` - public entrant profile pages resolve by slug (the identifier
  `aieb_analysis` snapshots actually key `per_entrant` by), not the internal UUID the pre-existing
  `GET /v1/entrants/{entrant_id}` uses - resolves to the most recent revision for that slug, since a
  snapshot carries no per-result revision pointer (disclosed limitation).
- `GET /v1/entrants/by-slug/{slug}/results` - real "results by release" cross-referencing (a linear
  scan over published snapshots; there is no per-entrant index yet, disclosed as not scaling past a
  small number of publications).
- `supersedes_id`, `created_at`, and `cohort_digest` added to `GET /v1/publications/{id}/results` -
  needed for the release detail page's changelog and cohort/evaluation-date metadata.
- `GET /v1/comparisons` reworked to accept `entrant_publication_ids` (one publication per entrant,
  falling back to the shared `publication_id`) for genuine cross-release comparison, and to check
  real cohort compatibility (`campaign.cohort_digest`) instead of unconditionally returning
  `cohort_comparable: true` - see "Comparison eligibility" below.

## Comparison eligibility and paired statistics (previously not implemented)

`GET /v1/comparisons` previously always returned `cohort_comparable: true` regardless of input.
Fixed: entrants within the same publication are trivially comparable (same frozen cohort by
construction, no digest bookkeeping needed); entrants from different publications are compared by
their campaigns' `cohort_digest` - equal and non-null means comparable, anything else does not, and
`non_comparable_reason` explains why. When comparable, the response now also includes
`paired_differences` - a per-task rate difference for each pair of eligible entrants, derived from
the same `per_task` cells each entrant's own snapshot already has. This is a same-cohort per-task
rate comparison, not a trial-repetition-matched pairing in the stricter sense `aieb_analysis.
paired_project_difference` computes for raw observations - the persisted snapshot does not carry
per-repetition detail, only aggregated `per_task` cells, so building genuine repetition-matched
pairing into a publication snapshot is real future work, disclosed rather than silently
overclaimed. When not comparable, entrants still get their own eligible aggregate (separate panels)
and no diff table - never a fabricated calculated winner (spec journey 6.1).

`Compare.tsx` lets an entrant be pinned to a specific other publication (`entrant_publication_ids`
in the URL), making cross-release comparison a real, reachable UI path, not merely a documented gap.

## Results table (previously missing most of the spec'd columns)

`Results.tsx` now derives, from the same typed `AnalysisSnapshot` the API returns: resolved-task
estimate (count of `all_k` cells) over total tasks attempted, valid trial count (summed `n`),
per-category rate columns (when `per_category` is present), plus the existing rate/coverage/cost/
time/limitations summary. "Planned" trial counts and per-entrant median engineering time/cost
remain unavailable per entrant - only a suite-wide total exists in the current analysis output, not
a per-entrant breakdown - shown as "Unknown"/suite-wide rather than fabricated.

## Pagination (previously wrong once results paginate)

`GET /v1/releases` and `GET /v1/corrections` were ordered oldest-first; Home/Results selected "the
last item of the first page" as "the latest," which is only correct with a single page. Reproduced
directly with three seeded publications and `limit=1`: the first page returned the OLDEST one, not
the newest. Fixed by ordering both queries newest-first (with the cursor comparison flipped to
match) - Home/Results now correctly take the first page's first item. `TaskCatalog`, `ReleasesList`,
and `Corrections` all gained a real "Next page" control following `next_cursor`, where none existed
before.

## Known, disclosed gaps that remain (not fabricated data)

- **Run evidence** (`/runs/:trialId`): the only backing endpoint, `GET /v1/trials/{id}`, is
  role-gated with no public-redacted branch - still shows an honest "requires authorization" state.
  Building a real redaction boundary is its own design task (ENG016-002), not attempted here.
- **Task ticket text**: `instruction.md`'s narrative (symptom/user impact) is not persisted on
  `task_revision` and so is not returned by any endpoint; the task detail page shows the full
  manifest (source/environment/application/submission/requirements), which is real and complete,
  but not the prose ticket text.
- **Methodology**: still real static content; no versioned `ProtocolRevision` history exists to
  query.
- Candidate logs/diffs: no endpoint returns them yet (same gap as run evidence's thin response), so
  there is no page rendering that content to test malicious-log escaping against - the existing
  malicious-content test uses a real API field (a task requirement description) instead.
- Real-browser visual/responsive inspection: no visual browser tooling was available in this
  environment.

## Testing

`apps/web/src/pages/*.test.tsx`: 26 tests (Vitest + Testing Library), against a typed spy on the API
client (`src/test/mockApi.ts` - MSW's network interception did not reliably patch `fetch` under this
environment's jsdom + very-recent-Node combination, so this session mocks at the typed-client
boundary instead; still real hook/component/render code):

- Every page: `Home`, `Results`, `Compare`, `TaskCatalog`, `TaskDetail`, `EntrantProfile`,
  `ReleasesList`, `ReleaseDetail`, `Corrections`, `RunEvidence` has at least one test.
- Honest empty/error/withdrawn/superseded/not-found/authorization-required states throughout -
  UI-01's "no placeholder leaderboard scores."
- A working error-recovery Retry (`Home`): a failed query followed by a successful refetch.
- Cohort-comparable vs. non-comparable comparison, including real `paired_differences` rendering.
- Null rates sort last and display "Unknown," never a fabricated zero.
- Up-to-4 entrant selection with the 5th checkbox disabled.
- URL-backed category filter and cursor-based "Next page" pagination.
- Malicious content (embedded `<img onerror>` and raw ANSI escape bytes) renders as literal text and
  never executes.
- Structural accessibility (`axe-core`: label association, table semantics, landmark structure -
  color-contrast disabled since jsdom cannot evaluate real color).

`services/api`'s own test suite (36 tests in `tests/test_api_service.py`, 3 in
`tests/test_api_cors.py`) covers: CORS allow/deny, newest-first pagination across multiple pages,
snapshot-shape validation (a digest-matching but structurally wrong snapshot is rejected), real
cohort-comparability (same-publication always comparable; cross-publication requires matching
`cohort_digest`) with real paired-difference output, `supersedes_id`/`cohort_digest`/`created_at` on
publication results, and the task-catalog/corrections listings from the first pass.

A real `uvicorn` instance was started against the real test Postgres and hit directly with `curl`
(including a real CORS preflight with an `Origin` header, and the new `/v1/entrants/by-slug/*`
routes) to confirm the generated TypeScript types match actual runtime response shapes.

## Commands

```powershell
cd apps/web
npm install
npm run dev            # local dev server, defaults to http://localhost:8000 for the API
npm run build           # tsc -b && vite build
npm test                 # vitest run
npm run lint:types      # tsc --noEmit
npm run generate:api-types  # regenerate src/api/schema.ts from the checked-in OpenAPI schema
```

Server-side: set `AIEB_CORS_ALLOWED_ORIGINS` if serving the website from anything other than the
default local dev origins (see `docs/implementation/evidence/ENG-014/api-service.md`).
