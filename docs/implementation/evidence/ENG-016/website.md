# ENG-016 evidence: public website

`apps/web` is a React 19 / TypeScript / Vite single-page app implementing Prompt 13's read-only
public routes: `/`, `/results`, `/compare`, `/entrants/:revision`, `/tasks`, `/tasks/:slug/:version`,
`/runs/:trialId`, `/methodology`, `/releases`, `/releases/:publicationId`, `/corrections`, `/docs`.
`/admin/*` is explicitly out of scope (ENG-017/018, Prompt 14).

## API integration

Types are generated (`openapi-typescript`, pinned) from the checked-in
`docs/implementation/evidence/ENG-014/openapi.json`, not hand-written - `src/api/schema.ts` is
regenerated via `npm run generate:api-types`, mirroring `scripts/generate_typescript_client.py`'s
approach for the checked-in evidence copy. All fetch calls go through `src/api/client.ts`'s typed
`api` client (openapi-fetch); nothing is recomputed client-side (every number rendered is either a
raw API field or a value the shared `aieb_analysis` package already computed server-side).

Three read endpoints did not exist before this ticket and were added to `services/api` because the
website genuinely needs them (not because ENG-016 owns backend persistence - the monorepo table
still says `apps/web` gets "generated API types; no scoring implementation"):

- `GET /v1/tasks` - a thin, public-safe task-catalog projection (slug/version/family_id/category/
  activity), filterable by category. `task_revision` has no retired/deprecated status column, so
  the catalog cannot filter on that (spec section 5 names it) - a disclosed schema gap.
- `GET /v1/corrections` - publications that supersede an earlier one or were withdrawn. There is no
  free-text "reason" column on `publication` yet, so this shows that a correction happened, not why.
- `supersedes_id` added to the existing `GET /v1/publications/{id}/results` response, needed for
  the release detail page's changelog.

## Known, disclosed gaps (not fabricated data)

- **Run evidence** (`/runs/:trialId`): the only backing endpoint, `GET /v1/trials/{id}`, is role-gated
  (operator/reviewer/administrator) with no public-redacted branch implemented - the page shows an
  honest "requires authorization" state for anonymous visitors rather than inventing a redaction
  boundary. The authorized response itself is also thin (id/phase/terminal_status only); the richer
  evidence tabs the spec names (diff, checks, usage) are not yet returned by any endpoint.
- **Methodology**: there is no persisted, versioned `ProtocolRevision` history to query - this page
  is real static content describing the one protocol this repository currently implements, not a
  stub standing in for a versioned API.
- **Entrant profile "results by release"**: cross-referencing every publication an entrant appears
  in has no dedicated query yet; the page links back to Results instead of fabricating that view.
- **Compare across releases**: the existing `/v1/comparisons` endpoint only accepts one
  `publication_id`, so entrants can only be compared within one publication's frozen cohort (they
  are inherently paired-comparable there by construction). Spec's "cross-release comparison shows
  separate panels with a non-comparable label" needs a backend endpoint accepting two publication
  IDs, which does not exist; not built speculatively here.

None of these gaps are silently papered over - each page states the limitation inline where a
reader would otherwise expect data.

## Testing

`apps/web/src/pages/*.test.tsx` (11 tests, Vitest + Testing Library) exercise the real hook/query/
render code against a typed spy on the API client (`src/test/mockApi.ts`) - MSW's network-level
interception was tried first but did not reliably patch `fetch` under this environment's jsdom +
very-recent-Node combination, so this session pivoted to mocking at the typed-client boundary
instead, still real component/query code, just not the real network layer:

- Honest empty states: no publication (`Home`), empty cohort (`Results`), empty catalog and
  empty search (`TaskCatalog`) - UI-01's "no placeholder leaderboard scores."
- Withdrawn-snapshot notice rendering.
- Null rates sort last and display "Unknown," never a fabricated zero.
- Up-to-4 entrant selection with the 5th checkbox disabled once 4 are selected.
- URL-backed category filter (permalink/shareable-view behavior).
- Malicious content (embedded `<img onerror>` and raw ANSI escape bytes) renders as literal text
  and never executes - proven by asserting a global flag the `onerror` handler would have set stays
  false, and that no `<img>` element exists in the DOM.
- Structural accessibility (`axe-core` against the real rendered DOM: label association, table
  semantics, landmark structure - color-contrast disabled since jsdom cannot evaluate real color).

Separately, `services/api`'s own test suite covers the three new/changed endpoints against a real
disposable PostgreSQL instance (`tests/test_api_service.py`): task-catalog projection and category
filtering, corrections listing only superseding/withdrawn publications (never an unrelated
published one), and `supersedes_id` appearing/being null correctly.

A real `uvicorn` instance was started against the real test Postgres and hit directly with `curl`
for `/v1/releases`, `/v1/tasks`, and `/v1/corrections` to confirm the generated TypeScript types
match actual runtime response shapes, not just the schema in isolation.

Not done in this pass: real-browser visual/responsive inspection (no visual browser tooling was
available in this environment) and a Storybook setup (spec allows fixtures there but none exists
yet - not required for the read-only journey this ticket covers).

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
