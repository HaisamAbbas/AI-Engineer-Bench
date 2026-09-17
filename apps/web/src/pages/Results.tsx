import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useReleases, usePublicationResults } from "../api/hooks";
import { Loading, ErrorState, EmptyState } from "../components/QueryStates";
import { formatRate, formatUsd, formatSeconds, formatUtc, formatCount } from "../lib/format";

const MAX_COMPARE = 4;

// Derived directly from the hook's own (generated, typed) return value
// rather than a separately-declared alias, so it can never structurally
// drift from what usePublicationResults actually returns.
type PublicationResultsData = NonNullable<ReturnType<typeof usePublicationResults>["data"]>;

interface EntrantRow {
  entrantId: string;
  suiteRate: number | null;
  // null (not 0) means "unavailable in this historical snapshot" -
  // per_entrant_total_tasks/resolved_tasks/valid_trials are whole-field
  // null on a snapshot published before they existed (review finding #1,
  // second pass); a fabricated 0 would misreport that as "zero tasks."
  resolvedTasks: number | null;
  totalTasks: number | null;
  validTrials: number | null;
  costPerResolution: number | null;
  verifierCostUsd: number | null;
  medianEngineeringSeconds: number | null;
  deadlineRate: number | null;
  infrastructureAttrition: number | null;
  categories: [string, number | null][];
}

// A coverage COUNT (resolved tasks, valid trials): if the whole per-entrant
// map is null the snapshot predates the field entirely - genuinely
// unavailable (null). If the map is present but this entrant's slug is
// absent, the entrant was scheduled (frozen) but never observed - a genuine
// zero, not unavailable (review findings #1/#3, third pass).
function coverageCount(field: Record<string, number> | null | undefined, slug: string): number | null {
  if (field == null) return null;
  return field[slug] ?? 0;
}

// An optional METRIC (rate, cost, time): null both when the whole map is
// absent (legacy snapshot) and when this entrant has no value (unobserved -
// e.g. cost per resolution is undefined with zero resolutions). Never a
// fabricated zero for either.
function optionalMetric<T>(field: Record<string, T | null> | null | undefined, slug: string): T | null {
  if (field == null) return null;
  return field[slug] ?? null;
}

/** Rows come from the UNION of the frozen entrant roster (authoritative -
 * `data.frozen_entrants`, from `campaign.resolved`) and any observed entrant,
 * so a frozen entrant with ZERO observations still gets a row showing
 * incomplete coverage rather than vanishing (review finding #3, third pass).
 * The total-task denominator is `data.frozen_tasks.length` (the frozen plan -
 * every entrant is scheduled against every task by construction), NOT a
 * per-entrant count derived from observed cells; the snapshot itself is never
 * mutated on the server, so we read the frozen counts from these sibling
 * fields (review finding #1, third pass). Every metric is read straight off
 * the typed response - none recomputed from per_task cells. */
function buildEntrantRows(data: PublicationResultsData): EntrantRow[] {
  const snapshot = data.snapshot;
  const totalTasks = data.frozen_tasks.length > 0 ? data.frozen_tasks.length : null;
  const roster: string[] = [];
  const seen = new Set<string>();
  for (const entrant of data.frozen_entrants) {
    if (!seen.has(entrant.slug)) {
      seen.add(entrant.slug);
      roster.push(entrant.slug);
    }
  }
  for (const slug of Object.keys(snapshot.per_entrant)) {
    if (!seen.has(slug)) {
      seen.add(slug);
      roster.push(slug);
    }
  }
  return roster.map((entrantId) => {
    const categories: [string, number | null][] = snapshot.per_category
      ? Object.entries(snapshot.per_category).map(([category, byEntrant]) => [category, byEntrant[entrantId] ?? null])
      : [];
    return {
      entrantId,
      suiteRate: snapshot.per_entrant[entrantId] ?? null,
      resolvedTasks: coverageCount(snapshot.per_entrant_resolved_tasks, entrantId),
      totalTasks,
      validTrials: coverageCount(snapshot.per_entrant_valid_trials, entrantId),
      costPerResolution: optionalMetric(snapshot.per_entrant_cost_per_resolution, entrantId),
      verifierCostUsd: optionalMetric(snapshot.per_entrant_verifier_cost_usd, entrantId),
      medianEngineeringSeconds: optionalMetric(snapshot.per_entrant_median_engineering_seconds, entrantId),
      deadlineRate: optionalMetric(snapshot.per_entrant_deadline_rate, entrantId),
      infrastructureAttrition: optionalMetric(snapshot.per_entrant_infrastructure_attrition, entrantId),
      categories,
    };
  });
}

/** "/results" - cohort selector (which publication), results table with the
 * columns spec section 5 names (entrant revision, resolved-task estimate
 * labeled with the real k, valid trials, per-entrant cost/verifier-cost/
 * median-engineering-time/deadline-rate/infrastructure-attrition,
 * per-category rates, coverage, evaluation date), sort, select up to 4
 * entrants, download the full provenance bundle (publication id, snapshot/
 * cohort digests, frozen task list, cohort identity, snapshot). Every number
 * is read directly from the typed AnalysisSnapshot the API returns (review
 * finding #2: per-entrant cost/time/deadline/attrition breakdowns are now
 * real `aieb_analysis.metrics.summarize()` output fields, mirroring how
 * `per_entrant`/`per_category` already group by entrant, not blended
 * suite-wide numbers repeated on every row) - none are recomputed here
 * (spec: "Do not recompute scores in JavaScript").
 *
 * "Planned" trial counts (as opposed to valid ones) and a per-entrant
 * aggregate Wilson uncertainty interval remain unavailable: the former
 * needs `planned_cells` wired to a real caller (ENG-011, disclosed
 * separately), and the latter cannot be soundly computed by pooling
 * per-task confidence intervals without a declared hierarchical model - both
 * are listed in `snapshot.limitations` rather than fabricated. */
export function Results() {
  const [params, setParams] = useSearchParams();
  const releases = useReleases();
  const publicationId = params.get("publication") ?? undefined;
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [sortBy, setSortBy] = useState<"entrant" | "rate">("entrant");

  const effectivePublicationId = useMemo(() => {
    if (publicationId) return publicationId;
    if (releases.data && releases.data.items.length > 0) return releases.data.items[0].id;
    return undefined;
  }, [publicationId, releases.data]);

  const results = usePublicationResults(effectivePublicationId);

  if (releases.isPending) return <Loading label="releases" />;
  if (releases.isError) return <ErrorState error={releases.error} onRetry={() => releases.refetch()} />;

  if (releases.data.items.length === 0) {
    return (
      <section>
        <h1>Results</h1>
        <EmptyState title="No published cohort yet.">
          <p>No campaign has been published, so there is no results table to show. This is the empty state - not a
          placeholder leaderboard.</p>
        </EmptyState>
      </section>
    );
  }

  return (
    <section>
      <h1>Results</h1>
      <label htmlFor="publication-select">Publication</label>
      <select
        id="publication-select"
        value={effectivePublicationId}
        onChange={(event) => setParams({ publication: event.target.value })}
      >
        {releases.data.items.map((publication) => (
          <option key={publication.id} value={publication.id}>
            {publication.id} ({formatUtc(publication.created_at).display})
          </option>
        ))}
      </select>

      {results.isPending && <Loading label="results" />}
      {results.isError && <ErrorState error={results.error} onRetry={() => results.refetch()} />}
      {results.isSuccess && (
        <ResultsTable
          data={results.data}
          publicationCreatedAt={releases.data.items.find((p) => p.id === effectivePublicationId)?.created_at}
          selected={selected}
          setSelected={setSelected}
          sortBy={sortBy}
          setSortBy={setSortBy}
          publicationId={effectivePublicationId!}
        />
      )}
    </section>
  );
}

function ResultsTable({
  data,
  publicationCreatedAt,
  selected,
  setSelected,
  sortBy,
  setSortBy,
  publicationId,
}: {
  data: PublicationResultsData;
  publicationCreatedAt: string | undefined;
  selected: Set<string>;
  setSelected: (s: Set<string>) => void;
  sortBy: "entrant" | "rate";
  setSortBy: (s: "entrant" | "rate") => void;
  publicationId: string;
}) {
  const { snapshot, status } = data;
  const notice = data.notice ?? undefined;
  const rows = buildEntrantRows(data);
  const categoryNames = snapshot.per_category ? Object.keys(snapshot.per_category).sort() : [];
  const allKLabel = snapshot.required_repetitions ? `Resolved tasks (all-${snapshot.required_repetitions})` : "Resolved tasks (all-k)";

  if (rows.length === 0) {
    return (
      <EmptyState title="This publication's cohort has no entrant results yet.">
        <p>The snapshot exists but contains no per-entrant data - an empty cohort, not a fetch failure.</p>
      </EmptyState>
    );
  }

  const sorted = [...rows].sort((a, b) => {
    if (sortBy === "rate") {
      const rateA = a.suiteRate ?? -1;
      const rateB = b.suiteRate ?? -1;
      return rateB - rateA;
    }
    return a.entrantId.localeCompare(b.entrantId);
  });

  function toggle(entrantId: string) {
    const next = new Set(selected);
    if (next.has(entrantId)) {
      next.delete(entrantId);
    } else if (next.size < MAX_COMPARE) {
      next.add(entrantId);
    }
    setSelected(next);
  }

  function downloadJson() {
    // The whole PublicationResultsResponse, not only `snapshot` - the
    // publication id, snapshot digest, cohort/protocol identity, and dates
    // shown on screen must also be in the downloaded bundle (spec: downloads
    // carry the publication ID and protocol hash shown on screen; review
    // finding #6, a bare `snapshot` download previously omitted all of it).
    const bundle = {
      publication_id: data.id,
      campaign_id: data.campaign_id,
      snapshot_digest: data.snapshot_digest,
      status: data.status,
      supersedes_id: data.supersedes_id,
      published_at: data.created_at,
      cohort_digest: data.cohort_digest,
      cohort: data.cohort,
      protocol_scoring_digest: data.protocol_scoring_digest,
      frozen_tasks: data.frozen_tasks,
      frozen_entrants: data.frozen_entrants,
      snapshot: data.snapshot,
    };
    const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${publicationId}-results.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div>
      {status === "withdrawn" && (
        <p role="alert">This snapshot has been withdrawn; it remains addressable but is not canonical.</p>
      )}
      {notice && <p role="alert">{notice}</p>}
      <dl className="suite-summary">
        <dt>Coverage</dt>
        <dd>
          <span className={snapshot.complete_for_rank ? "badge badge-complete" : "badge badge-incomplete"}>
            {snapshot.complete_for_rank ? "Complete" : "Incomplete"}
          </span>
        </dd>
        {data.cohort && (
          <>
            <dt>Suite / track</dt>
            <dd>
              {data.cohort.suite_id} / {data.cohort.track}
            </dd>
            <dt>Protocol / dependency mode</dt>
            <dd>
              {data.cohort.protocol_id} / {data.cohort.dependency_mode}
            </dd>
            <dt>Hardware class</dt>
            <dd>{data.cohort.hardware_class}</dd>
            <dt>Budget / model profile</dt>
            <dd>
              {data.cohort.budget_profile_id} / {data.cohort.application_model_profile.model_profile_id}
            </dd>
            <dt>Required capabilities</dt>
            <dd>{data.cohort.required_capabilities.join(", ")}</dd>
          </>
        )}
        <dt>Cohort digest (provenance)</dt>
        <dd className="tabular-nums">{data.cohort_digest ?? "Unknown"}</dd>
        <dt>Snapshot digest (provenance)</dt>
        <dd className="tabular-nums">{data.snapshot_digest}</dd>
        <dt>Protocol scoring digest (provenance)</dt>
        <dd className="tabular-nums">{data.protocol_scoring_digest ?? "Unknown"}</dd>
        <dt>Suite rate (fixed-weight, all entrants)</dt>
        <dd className="tabular-nums">{formatRate(snapshot.suite_rate)}</dd>
        <dt>Cost per resolution (suite-wide)</dt>
        <dd className="tabular-nums">{formatUsd(snapshot.cost_per_resolution)}</dd>
        <dt>Verifier cost (suite-wide)</dt>
        <dd className="tabular-nums">{formatUsd(snapshot.verifier_cost_total_usd)}</dd>
        <dt>Total campaign cost (incl. invalid attempts)</dt>
        <dd className="tabular-nums">{formatUsd(snapshot.total_campaign_cost_usd)}</dd>
        <dt>Median successful engineering time (suite-wide)</dt>
        <dd className="tabular-nums">{formatSeconds(snapshot.successful_engineering_median_seconds)}</dd>
        <dt>Deadline rate</dt>
        <dd className="tabular-nums">{formatRate(snapshot.deadline_rate)}</dd>
        <dt>Infrastructure attrition</dt>
        <dd className="tabular-nums">{formatRate(snapshot.infrastructure_attrition)}</dd>
        {/* Publication time, not an evaluation date range - the campaign's
         * own actual run-window dates are not tracked yet (review finding
         * #4, second pass: labeling this "Evaluation date" implied a fact
         * this field doesn't carry). */}
        <dt>Published</dt>
        <dd>{publicationCreatedAt ? formatUtc(publicationCreatedAt).display : "Unknown"}</dd>
      </dl>
      {snapshot.limitations.length > 0 && (
        <ul className="limitations">
          {snapshot.limitations.map((limitation) => (
            <li key={limitation}>{limitation}</li>
          ))}
        </ul>
      )}
      <button type="button" onClick={downloadJson}>
        Download JSON
      </button>
      <div className="table-scroll" role="region" aria-label="Results by entrant revision" tabIndex={0}>
      <table>
        <caption>Results by entrant revision</caption>
        <thead>
          <tr>
            <th scope="col">
              <button type="button" onClick={() => setSortBy("entrant")}>
                Entrant
              </button>
            </th>
            <th scope="col">
              <button type="button" onClick={() => setSortBy("rate")}>
                Rate
              </button>
            </th>
            <th scope="col">{allKLabel}</th>
            <th scope="col">Valid trials</th>
            <th scope="col">Cost / resolution</th>
            <th scope="col">Verifier cost</th>
            <th scope="col">Median engineering time</th>
            <th scope="col">Deadline rate</th>
            <th scope="col">Infrastructure attrition</th>
            {categoryNames.map((category) => (
              <th scope="col" key={category}>
                {category} rate
              </th>
            ))}
            <th scope="col">Compare</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((row) => (
            <tr key={row.entrantId}>
              <th scope="row">
                <Link to={`/entrants/${row.entrantId}`}>{row.entrantId}</Link>
              </th>
              <td className="tabular-nums">{formatRate(row.suiteRate)}</td>
              <td className="tabular-nums">
                {formatCount(row.resolvedTasks)}/{formatCount(row.totalTasks)}
              </td>
              <td className="tabular-nums">{formatCount(row.validTrials)}</td>
              <td className="tabular-nums">{formatUsd(row.costPerResolution)}</td>
              <td className="tabular-nums">{formatUsd(row.verifierCostUsd)}</td>
              <td className="tabular-nums">{formatSeconds(row.medianEngineeringSeconds)}</td>
              <td className="tabular-nums">{formatRate(row.deadlineRate)}</td>
              <td className="tabular-nums">{formatRate(row.infrastructureAttrition)}</td>
              {row.categories.map(([category, rate]) => (
                <td className="tabular-nums" key={category}>
                  {formatRate(rate)}
                </td>
              ))}
              <td>
                <label>
                  <input
                    type="checkbox"
                    checked={selected.has(row.entrantId)}
                    disabled={!selected.has(row.entrantId) && selected.size >= MAX_COMPARE}
                    onChange={() => toggle(row.entrantId)}
                  />
                  <span className="visually-hidden">Select {row.entrantId} for comparison</span>
                </label>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
      {selected.size >= 2 && (
        <Link to={`/compare?publication=${publicationId}&entrants=${[...selected].join(",")}`}>
          Compare {selected.size} selected entrants
        </Link>
      )}
    </div>
  );
}
