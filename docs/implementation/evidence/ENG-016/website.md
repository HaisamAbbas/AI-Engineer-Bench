# ENG-016 evidence: public website

`apps/web` is a React 19 / TypeScript / Vite single-page app implementing Prompt 13's read-only
public routes: `/`, `/results`, `/compare`, `/entrants/:slug`, `/tasks`, `/tasks/:slug/:version`,
`/runs/:trialId`, `/methodology`, `/releases`, `/releases/:publicationId`, `/corrections`, `/docs`.
`/admin/*` is explicitly out of scope (ENG-017/018, Prompt 14).

Date: 2026-09-15, revised 2026-09-16 after a second independent review found six real gaps in the
first pass (see DECISIONS.md ENG016-003 through ENG016-006), then revised again the same day after
a third independent review found findings #2, #3, and #7 from that second pass were only partially
fixed (see DECISIONS.md ENG016-007 through ENG016-009, and "Third-pass fixes" below), then revised
a fourth time after a review of THAT pass found findings #2/#3/#4/#6 (that review's own numbering)
still partially overstated (see DECISIONS.md ENG016-010 through ENG016-012, and "Fourth-pass fixes"
below), then a fifth time after a review found the fourth pass had itself introduced two integrity
bugs plus two more gaps (see DECISIONS.md ENG016-013/014, and "Fifth-pass fixes" below). ENG-016
remains IN_PROGRESS: real HTTP/browser integration tests and broader accessibility coverage are
deliberately not attempted here - a real separate effort, disclosed as open rather than bundled
into this pass - along with the other open acceptance gates listed below.

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

## Third-pass fixes (2026-09-16, second independent review of the second pass)

A third review found findings #2, #3, and #7 from the prior review only partially addressed, plus
three new gaps (#4, #6, #7 in that review's own numbering). All fixed here except #5 (integration/
accessibility test coverage), disclosed as open below.

**Cross-release comparison still violated spec journey 6.1** ("A cross-release comparison shows
separate panels with a non-comparable label, never a calculated winner" - unconditional, not
"unless the cohorts happen to match"). The prior fix treated a matching `campaign.cohort_digest`
across two different publications as sufficient proof of comparability and computed paired
differences across them - but `Cohort` records the frozen track/suite/protocol/budget/hardware
identity, not the exact resolved task list, entrant revisions, or repetition plan, so a matching
digest never actually proved matching observations. Fixed: `GET /v1/comparisons` now treats ANY
comparison across different publications as unconditionally non-comparable, regardless of
`cohort_digest` - paired differences are only ever computed when every entrant comes from the SAME
publication. The `TaskPairedDifference` schema's own docstring was also corrected: it is a per-task
rate difference from each entrant's own aggregated `per_task` cell, NOT the project/family-resampled,
repetition-matched statistic spec section 28 describes (`aieb_analysis.paired_project_difference`) -
that requires per-repetition observations grouped by underlying project, and nothing in the hosted
persistence schema populates the `project_id` that function's own signature requires (a real,
disclosed structural gap, not attempted here).

**The Results table's per-entrant metrics were still suite-wide numbers repeated on every row**, and
resolved-task/valid-trial counts were recomputed in the browser rather than returned by the API.
Fixed at the source: `aieb_analysis.metrics.summarize()` gained real per-entrant breakdowns -
`per_entrant_valid_trials`, `per_entrant_resolved_tasks`, `per_entrant_total_tasks`,
`per_entrant_cost_per_resolution`, `per_entrant_verifier_cost_usd`,
`per_entrant_median_engineering_seconds`, `per_entrant_deadline_rate`,
`per_entrant_infrastructure_attrition` - each mirrors the exact population/exclusion rule its
suite-wide counterpart already used, just grouped per entrant (the same pattern `per_entrant`/
`per_category` already established). `summarize()` also now echoes `required_repetitions`, so the
frontend can label "Resolved tasks (all-5)" with the real k instead of a generic "all-k".
`Results.tsx` now reads all of these directly off the typed `AnalysisSnapshot` - the JS-side
`buildEntrantRows` no longer iterates `per_task` cells to derive counts. Per-entrant coverage
against the frozen plan (valid vs *planned* trial counts) and a per-entrant aggregate Wilson
uncertainty interval remain unavailable and are named in `snapshot.limitations` rather than
fabricated: the former needs `planned_cells` wired to a real caller (ENG-011, a pre-existing
disclosed gap), the latter cannot be soundly computed by pooling per-task confidence intervals
without a declared hierarchical model.

**Publication provenance could be misrepresented two ways.** (1) `ReleaseDetail.tsx` inferred the
"frozen task list" from which tasks happened to have a `snapshot.per_task` cell - a planned task
with zero observations disappeared entirely, the exact case incomplete-coverage reporting must
preserve. Fixed: `GET /v1/publications/{id}/results` gained `frozen_tasks`, read from the campaign's
own frozen manifest (`campaign.resolved["tasks"]`), and a `cohort` object (track/suite_id/
protocol_id/dependency_mode/hardware_class from `campaign.resolved["cohort"]`) - real manifest data,
not inferred from result rows. A task with no observations still appears in the list, flagged "no
observations", rather than silently vanishing. (2) An entrant's "results by release" resolved every
historical result to whichever entrant revision is newest RIGHT NOW, even though a historical result
may have used an older revision. Fixed: `GET /v1/entrants/by-slug/{slug}/results` now looks up the
EXACT entrant revision that publication's own frozen campaign manifest used
(`campaign.resolved["entrants"]`) and returns it as `entrant_version` per row - pinned to the
configuration that actually produced that result, not a guess. The entrant profile page's header
(model/capabilities/etc.) still shows the most-recent revision - that remains a real, disclosed
limitation, now narrowed to just the header, not the per-result data too.

**Compare showed only an aggregate rate per entrant.** `Compare.tsx`'s `EntrantPanel` now also
fetches and shows each entrant's real configuration (version, model, capabilities via
`GET /entrants/by-slug/{slug}`) and, from that entrant's own publication's snapshot, the same
per-entrant cost/median-time/deadline-rate/coverage numbers the Results table shows - not merely
"Rate: X".

**Downloaded Results JSON was not an immutable publication bundle.** `Results.tsx`'s download
previously serialized only `snapshot`, omitting the publication ID, snapshot digest, cohort/protocol
identity, and dates shown on screen. Fixed: the download now includes the full
`PublicationResultsResponse` (publication id, campaign id, snapshot digest, status, supersedes_id,
created_at, cohort_digest, cohort, frozen_tasks, snapshot) - everything the page itself displays.

**A visible encoding defect** (`Â·` instead of `·` between Home's two links) is fixed by using a
JS unicode escape (`·`) directly in the JSX rather than a literal byte in the source file - this
makes the separator's correctness independent of any file/transport encoding layer, rather than
merely re-saving the same literal character and hoping the mojibake does not recur.

See DECISIONS.md ENG016-007 (comparison), ENG016-008 (per-entrant metrics/provenance), ENG016-009
(Compare/download/encoding) for full detail and test references.

## Fourth-pass fixes (2026-09-16, a review of the third pass)

A review of the third pass found four of its own claims (that review's findings #2, #3, #4, #6)
still partially overstated, plus a real correctness bug (#3) - see DECISIONS.md ENG016-010 through
ENG016-012 for full detail and test references.

**Per-entrant coverage still excluded zero-observation frozen tasks.** `per_entrant_total_tasks`
was computed by `aieb_analysis.metrics.summarize()` from DISTINCT OBSERVED tasks per entrant, since
that package has no access to the frozen plan - a task with zero observations for an entrant was
invisible to it entirely, so a two-task campaign with one unobserved task reported "1/1" instead of
"1/2", hiding exactly the incompleteness Release Detail's own frozen task list correctly showed.
Fixed at the API layer, where the frozen manifest actually is available: `GET /v1/publications/{id}/
results` now overrides `per_entrant_total_tasks` with `len(frozen_tasks)` for every entrant (every
entrant in a frozen campaign is scheduled against every frozen task - the cross product
`freeze_campaign` builds). Separately, the per-entrant fields' schema default changed from `{}` to
`None`: a snapshot published before these fields existed has no such data at all, which is a
different fact from "this field is present and every entrant happens to have zero of something" -
collapsing both into `{}` let the frontend's `?? 0` fallback render a fabricated `0` for genuinely
unavailable historical data. `Results.tsx`/`Compare.tsx` now render "Unknown" when the whole field
is `null`, never a fabricated count.

**Compare still showed the newest entrant configuration, not the selected publication's exact
one.** Every comparison panel called `GET /entrants/by-slug/{slug}`, which always resolves the
newest `EntrantRevisionRow` - a historical or cross-release panel could silently combine one
publication's metrics with a DIFFERENT, newer entrant revision's model/capabilities. Fixed with a
new `GET /v1/publications/{publication_id}/entrants/{slug}`, reading the frozen configuration
directly from `campaign.resolved["entrants"]` - pinned to that publication forever, unaffected by
any later revision of the same slug. `Compare.tsx`'s `EntrantPanel` now calls this instead.

**Median engineering time was wrong for an even sample.** Both the suite-wide and per-entrant
medians picked `times[len(times)//2]` (the upper-middle raw value) instead of a real median -
`[10, 20]` returned `20`, not `15`, and a test explicitly asserted `20`, locking the defect in as
expected behavior. Fixed with `statistics.median`; `successful_engineering_median_seconds` and
`per_entrant_median_engineering_seconds` are now `float`, since a real median of an even sample is
fractional.

**Provenance remained incomplete and partly mislabeled.** `CohortIdentity` omitted
`budget_profile_id`, `application_model_profile`, and `required_capabilities` - real fields on the
frozen `Cohort` this route already had access to. `hardware_class` was labeled "(profile)" on
screen, which is a distinct concept from the budget/application profile the spec actually asks for
- fixed by exposing both separately ("Hardware class" and "Budget / model profile"). Publication
`created_at` was labeled "Evaluation date," which it isn't (publication time, often well after
evaluation actually finished) - relabeled "Published," and a genuine evaluation window
(`evaluation_started_at`/`evaluation_completed_at`) was added, derived from real
`attempt.created_at` timestamps already in the database (min/max across the campaign's trials) -
not a new tracked concept, not fabricated. `protocol_scoring_digest` (from
`campaign.resolved["protocol"]["scoring_digest"]`) was also added, closing the "downloaded bundle
lacks an actual protocol/scoring digest" gap. The downloaded JSON bundle now includes all of this.

**Aggregate rate deltas were still labeled "paired task outcomes."** The backend's own docstrings
already disclosed these are not repetition-matched paired statistics, but the UI still called them
"Paired task outcomes" and the API field was `paired_differences` - overclaiming by name even with
an accurate docstring underneath. Renamed throughout: the API field is now `task_rate_deltas`, the
schema is `TaskRateDelta`, and the UI heading is "Per-task rate deltas."

Not attempted: the review's finding #6 (open acceptance gates - HTTP/browser integration tests,
valid/planned trial coverage, per-entrant uncertainty, public redacted run evidence, versioned
Methodology, task ticket text, candidate log/diff rendering, full-page accessibility/responsive
checks) lists real, already-disclosed gaps rather than a new claim to fix - see "Known, disclosed
gaps" below, unchanged in kind by this pass.

## Fifth-pass fixes (2026-09-16, a review of the fourth pass)

The fourth pass's own fixes introduced two High integrity bugs and left two Medium gaps - all fixed
(DECISIONS.md ENG016-013/014).

**The served snapshot no longer matched its digest.** The fourth pass corrected the coverage
denominator by rewriting `snapshot.per_entrant_total_tasks` at read time, then returned that mutated
snapshot beside the original `snapshot_digest` - so the response, and the downloaded bundle,
contained a digest that no longer hashed its own snapshot, breaking the immutable-publication
guarantee. Fixed: the snapshot is returned EXACTLY as stored, never touched on a read; the correct
frozen-plan total is served via the separate `frozen_tasks` list instead, and the frontend uses
`frozen_tasks.length` as the denominator. A test now recomputes the served snapshot's digest and
asserts it still equals `snapshot_digest`, so read-time mutation cannot silently return.

**The evaluation window was fabricated.** `evaluation_started_at`/`evaluation_completed_at` were
derived from `min`/`max(attempt.created_at)`, but `AttemptRow` records only row CREATION time (at
enqueue), never evaluation start or finish - so "completed" was just the latest attempt-row insert,
and a campaign with one long attempt reported a zero-duration window. Removed both fields entirely
rather than serving an accurate-sounding but invented value; a genuine window must wait for real
durable lifecycle timestamps. The real, frozen-manifest `protocol_scoring_digest` stays.

**A frozen entrant with zero observations still vanished from the table.** Rows were built from
`snapshot.per_entrant` alone. Fixed: the response now carries a `frozen_entrants` roster (from
`campaign.resolved["entrants"]`), and the results table builds rows from the union of that roster
and the observed entrants - an entrant that was scheduled but never ran shows a genuine 0 valid
trials and an "Unknown" aggregate, never a fabricated 0%. The null-vs-zero distinction is explicit:
a coverage count is `null` (unavailable) only for a legacy snapshot missing the whole field, and a
genuine `0` when the field is present but the entrant unobserved; a rate/cost/time metric is `null`
in both cases.

**Comparing the same slug across two releases corrupted the panels.** `ComparisonResponse.entrants`
was a dict keyed by entrant slug, so selecting `agent-a` from two different publications (the
natural "did it improve between releases?" comparison) overwrote one entry and rendered the same
aggregate in both panels. Fixed: `entrants` is now an ordered list of `ComparisonEntrantPanel`, one
per selection, each carrying its own `publication_id`; both the configuration and the metrics in a
panel are keyed to that publication, and selecting the exact same `(slug, publication)` twice
(comparing something to itself) is rejected with 400.

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
- **Genuine repetition-matched paired statistics** (spec section 28: "resampling projects/families
  then repetitions according to the declared hierarchical model", `aieb_analysis.
  paired_project_difference`): the hosted persistence schema (`trial`/`attempt`/`candidate`/
  `evaluation`) never tracks `project_id`, which that function's own signature requires - it has no
  production caller anywhere in this codebase. The per-task rate difference `GET /v1/comparisons`
  returns within a single publication is honestly labeled as exactly that, not this stricter
  statistic - building real per-repetition, project-tracked pairing is a persistence-schema change,
  not a bug fix, and is future work.
- **"Planned" trial counts** (as opposed to valid ones) and **a per-entrant aggregate Wilson
  uncertainty interval** are not in `AnalysisSnapshot` - the former needs `planned_cells` wired to a
  real caller (the same pre-existing ENG-011 gap), the latter is not statistically sound to compute
  by pooling per-task confidence intervals without a declared hierarchical model. Both are named in
  `snapshot.limitations`, not fabricated.
- **Frontend tests mock at the typed-API-client boundary, not real HTTP.** They exercise real hook/
  component/render code, but never a real `fetch`, generated-client serialization, CORS, or an
  actual running backend - and structural accessibility automation (`axe-core`) covers only Home and
  TaskCatalog, not every page. A real end-to-end journey (seeded API, real browser, every page) is a
  distinct, larger effort - deliberately not attempted in this pass (third review's finding #5),
  disclosed as open rather than silently claimed via the existing 27 typed-mock tests.

## Testing

`apps/web/src/pages/*.test.tsx`: 29 tests (Vitest + Testing Library), against a typed spy on the API
client (`src/test/mockApi.ts` - MSW's network interception did not reliably patch `fetch` under this
environment's jsdom + very-recent-Node combination, so this session mocks at the typed-client
boundary instead; still real hook/component/render code):

- Every page: `Home`, `Results`, `Compare`, `TaskCatalog`, `TaskDetail`, `EntrantProfile`,
  `ReleasesList`, `ReleaseDetail`, `Corrections`, `RunEvidence` has at least one test.
- Honest empty/error/withdrawn/superseded/not-found/authorization-required states throughout -
  UI-01's "no placeholder leaderboard scores."
- A working error-recovery Retry (`Home`): a failed query followed by a successful refetch.
- Cohort-comparable vs. non-comparable comparison, including real `paired_differences` rendering and
  Compare's per-entrant exact configuration (version/model/capabilities) plus cost/time/coverage.
- Null rates sort last and display "Unknown," never a fabricated zero.
- Up-to-4 entrant selection with the 5th checkbox disabled.
- URL-backed category filter and cursor-based "Next page" pagination.
- Malicious content (embedded `<img onerror>` and raw ANSI escape bytes) renders as literal text and
  never executes.
- Structural accessibility (`axe-core`: label association, table semantics, landmark structure -
  color-contrast disabled since jsdom cannot evaluate real color).

`services/api`'s own test suite (46 tests in `tests/test_api_service.py`, 3 in
`tests/test_api_cors.py`) covers: CORS allow/deny, newest-first pagination across multiple pages,
snapshot-shape validation (a digest-matching but structurally wrong snapshot is rejected), that the
SERVED snapshot still hashes to its recorded digest (never mutated on a read), real
cohort-comparability (same-publication always comparable; cross-publication is now UNCONDITIONALLY
non-comparable, including when `cohort_digest` happens to match) with real per-task rate-delta
output, comparing the same slug across two releases keeping both panels distinct (and rejecting the
same `(slug, publication)` twice), `supersedes_id`/`cohort_digest`/`protocol_scoring_digest`/`cohort`/
`frozen_tasks`/`frozen_entrants` on publication results, the frozen task count served separately with
the snapshot unmodified, a zero-observation frozen entrant still appearing in the roster, the
task-catalog/corrections listings, the frozen task list preserving a zero-observation task, and
entrant results pinning the exact revision a historical publication actually used. `aieb_analysis`'s
own `tests/test_analysis.py` (16 tests) covers the new per-entrant cost/time/deadline/attrition
breakdowns, `required_repetitions`, and the conventional (fractional, even-sample) median.

**On the Python test count**: a prior review correctly noted an environment without
`AIEB_DATABASE_URL` configured skips (not fails) every PostgreSQL-dependent test class
(`test_worker_leasing.py`, `test_attempt_lifecycle.py`'s DB-touching cases, `test_api_service.py`,
`test_api_cors.py`, and others) - stating a single "N tests passing" number without naming that
precondition is misleading. Against a real disposable Postgres instance
(`docker run ... postgres:16`, `AIEB_DATABASE_URL` set, `alembic upgrade head` applied): 125 tests
discovered, 124 passed, 1 skipped (a pre-existing, intentionally-skipped case unrelated to
`AIEB_DATABASE_URL`). Without `AIEB_DATABASE_URL` set: 125 discovered, 64 passed, 61 skipped - all
OK, none failed; the skips are the expected, honest behavior for a missing precondition, not a
hidden test failure.

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
