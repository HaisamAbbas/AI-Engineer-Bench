import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useReleases, usePublicationResults } from "../api/hooks";
import { Loading, ErrorState, EmptyState } from "../components/QueryStates";
import { formatRate, formatUsd, formatSeconds, formatUtc } from "../lib/format";

const MAX_COMPARE = 4;

interface Snapshot {
  per_task: Record<string, { s: number; n: number; rate: number | null; all_k: boolean | null }>;
  per_entrant: Record<string, number | null>;
  per_category: Record<string, Record<string, number | null>> | null;
  complete_for_rank: boolean;
  suite_rate: number | null;
  cost_per_resolution: number | null;
  successful_engineering_median_seconds: number | null;
}

/** "/results" - cohort selector (which publication), results table, sort,
 * select up to 4 entrants, download JSON. Numbers are exactly what the API
 * returns (the shared aieb_analysis output shape) - none are recomputed
 * here (spec 4/35). */
export function Results() {
  const [params, setParams] = useSearchParams();
  const releases = useReleases();
  const publicationId = params.get("publication") ?? undefined;
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [sortBy, setSortBy] = useState<"entrant" | "rate">("entrant");

  const effectivePublicationId = useMemo(() => {
    if (publicationId) return publicationId;
    if (releases.data && releases.data.items.length > 0) {
      return (releases.data.items[releases.data.items.length - 1] as { id: string }).id;
    }
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
        {releases.data.items.map((item) => {
          const publication = item as { id: string; created_at: string };
          return (
            <option key={publication.id} value={publication.id}>
              {publication.id} ({formatUtc(publication.created_at).display})
            </option>
          );
        })}
      </select>

      {results.isPending && <Loading label="results" />}
      {results.isError && <ErrorState error={results.error} onRetry={() => results.refetch()} />}
      {results.isSuccess && (
        <ResultsTable
          snapshot={results.data.snapshot as Snapshot}
          status={results.data.status as string}
          notice={(results.data as { notice?: string }).notice}
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
  snapshot,
  status,
  notice,
  selected,
  setSelected,
  sortBy,
  setSortBy,
  publicationId,
}: {
  snapshot: Snapshot;
  status: string;
  notice: string | undefined;
  selected: Set<string>;
  setSelected: (s: Set<string>) => void;
  sortBy: "entrant" | "rate";
  setSortBy: (s: "entrant" | "rate") => void;
  publicationId: string;
}) {
  const entrantIds = Object.keys(snapshot.per_entrant ?? {});

  if (entrantIds.length === 0) {
    return (
      <EmptyState title="This publication's cohort has no entrant results yet.">
        <p>The snapshot exists but contains no per-entrant data - an empty cohort, not a fetch failure.</p>
      </EmptyState>
    );
  }

  const sorted = [...entrantIds].sort((a, b) => {
    if (sortBy === "rate") {
      const rateA = snapshot.per_entrant[a] ?? -1;
      const rateB = snapshot.per_entrant[b] ?? -1;
      return rateB - rateA;
    }
    return a.localeCompare(b);
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
    const blob = new Blob([JSON.stringify(snapshot, null, 2)], { type: "application/json" });
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
      <p>
        Coverage:{" "}
        <span className={snapshot.complete_for_rank ? "badge badge-complete" : "badge badge-incomplete"}>
          {snapshot.complete_for_rank ? "Complete" : "Incomplete"}
        </span>
        {" · "}Suite rate: {formatRate(snapshot.suite_rate)}
        {" · "}Cost per resolution: {formatUsd(snapshot.cost_per_resolution)}
        {" · "}Median engineering time: {formatSeconds(snapshot.successful_engineering_median_seconds)}
      </p>
      <button type="button" onClick={downloadJson}>
        Download JSON
      </button>
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
            <th scope="col">Compare</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((entrantId) => (
            <tr key={entrantId}>
              <th scope="row">
                <Link to={`/entrants/${entrantId}`}>{entrantId}</Link>
              </th>
              <td className="tabular-nums">{formatRate(snapshot.per_entrant[entrantId])}</td>
              <td>
                <label>
                  <input
                    type="checkbox"
                    checked={selected.has(entrantId)}
                    disabled={!selected.has(entrantId) && selected.size >= MAX_COMPARE}
                    onChange={() => toggle(entrantId)}
                  />
                  <span className="visually-hidden">Select {entrantId} for comparison</span>
                </label>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {selected.size >= 2 && (
        <Link to={`/compare?publication=${publicationId}&entrants=${[...selected].join(",")}`}>
          Compare {selected.size} selected entrants
        </Link>
      )}
    </div>
  );
}
