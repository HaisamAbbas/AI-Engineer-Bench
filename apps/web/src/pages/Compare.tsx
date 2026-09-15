import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { usePublicationResults, useComparison } from "../api/hooks";
import { Loading, ErrorState, EmptyState } from "../components/QueryStates";
import { formatRate, formatPercentagePointDifference } from "../lib/format";

/** "/compare" - up to 4 entrants side by side, paired task outcomes when
 * cohort-comparable, and genuine cross-release comparison: an entrant can
 * be pinned to a specific OTHER publication via `entrant_publication_ids`
 * (same order as `entrant_ids`, empty string falling back to the shared
 * `publication`). Cohort compatibility is real (GET /v1/comparisons checks
 * campaign.cohort_digest), not assumed - an incompatible pairing shows
 * separate panels and a non-comparable label, never a fabricated winner
 * (spec journey 6.1). */
export function Compare() {
  const [params, setParams] = useSearchParams();
  const publicationId = params.get("publication") ?? undefined;
  const entrantIds = (params.get("entrants") ?? "").split(",").filter(Boolean);
  const entrantPublicationIdsRaw = params.get("entrant_publications");
  const entrantPublicationIds = entrantPublicationIdsRaw ? entrantPublicationIdsRaw.split(",") : undefined;

  const [newEntrantId, setNewEntrantId] = useState("");
  const [newPublicationId, setNewPublicationId] = useState("");

  const anchorResults = usePublicationResults(publicationId);
  const comparison = useComparison(publicationId, entrantIds, entrantPublicationIds);

  if (!publicationId && !entrantPublicationIds) {
    return (
      <section>
        <h1>Compare</h1>
        <EmptyState title="Select 2 to 4 entrants to compare.">
          <p>Go to Results, select entrants with the checkboxes, then choose "Compare selected entrants".</p>
        </EmptyState>
      </section>
    );
  }

  if (entrantIds.length < 2) {
    return (
      <section>
        <h1>Compare</h1>
        <EmptyState title="Select at least 2 entrants to compare." />
      </section>
    );
  }

  if (entrantIds.length > 4) {
    return (
      <section>
        <h1>Compare</h1>
        <EmptyState title="At most 4 entrants can be compared at once.">
          <p>{entrantIds.length} were requested via the URL.</p>
        </EmptyState>
      </section>
    );
  }

  function updateUrl(nextEntrantIds: string[], nextEntrantPublications: (string | undefined)[]) {
    const next: Record<string, string> = {};
    if (publicationId) next.publication = publicationId;
    next.entrants = nextEntrantIds.join(",");
    if (nextEntrantPublications.some((p) => p)) {
      next.entrant_publications = nextEntrantIds.map((_, i) => nextEntrantPublications[i] ?? "").join(",");
    }
    setParams(next);
  }

  function removeEntrant(id: string) {
    const index = entrantIds.indexOf(id);
    const nextIds = entrantIds.filter((e) => e !== id);
    const nextPubs = (entrantPublicationIds ?? entrantIds.map(() => undefined)).filter((_, i) => i !== index);
    updateUrl(nextIds, nextPubs);
  }

  function addEntrantFromRelease() {
    if (!newEntrantId || !newPublicationId || entrantIds.length >= 4) return;
    const nextIds = [...entrantIds, newEntrantId];
    const nextPubs = [...(entrantPublicationIds ?? entrantIds.map(() => undefined)), newPublicationId];
    updateUrl(nextIds, nextPubs);
    setNewEntrantId("");
    setNewPublicationId("");
  }

  return (
    <section>
      <h1>Compare</h1>
      {anchorResults.isError && <ErrorState error={anchorResults.error} onRetry={() => anchorResults.refetch()} />}
      {comparison.isPending && <Loading label="comparison" />}
      {comparison.isError && <ErrorState error={comparison.error} onRetry={() => comparison.refetch()} />}
      {comparison.isSuccess && (
        <div className="compare-panels">
          {!comparison.data.cohort_comparable && (
            <p role="alert">
              These entrants are not cohort-comparable{comparison.data.non_comparable_reason ? `: ${comparison.data.non_comparable_reason}` : ""}.
              Paired statistics are disabled; each entrant's own result is still shown separately.
            </p>
          )}
          <div className="compare-grid">
            {entrantIds.map((entrantId) => {
              const entry = comparison.data.entrants[entrantId];
              return (
                <article key={entrantId} className="compare-panel">
                  <h2>{entrantId}</h2>
                  <button type="button" onClick={() => removeEntrant(entrantId)}>
                    Remove
                  </button>
                  {entry.eligible ? (
                    <p>Rate: {formatRate(entry.aggregate)}</p>
                  ) : (
                    <p>Not eligible: {entry.reason}</p>
                  )}
                </article>
              );
            })}
          </div>
          {comparison.data.cohort_comparable && comparison.data.paired_differences && (
            <PairedDifferences pairs={comparison.data.paired_differences} />
          )}
        </div>
      )}

      {entrantIds.length < 4 && (
        <fieldset>
          <legend>Add an entrant from another release (cross-release comparison)</legend>
          <label htmlFor="new-entrant-id">Entrant slug</label>
          <input id="new-entrant-id" value={newEntrantId} onChange={(e) => setNewEntrantId(e.target.value)} />
          <label htmlFor="new-publication-id">Publication ID</label>
          <input id="new-publication-id" value={newPublicationId} onChange={(e) => setNewPublicationId(e.target.value)} />
          <button type="button" onClick={addEntrantFromRelease}>
            Add
          </button>
        </fieldset>
      )}

      <p>
        <button type="button" onClick={() => navigator.clipboard?.writeText(window.location.href)}>
          Copy URL
        </button>
      </p>
    </section>
  );
}

function PairedDifferences({
  pairs,
}: {
  pairs: NonNullable<ReturnType<typeof useComparison>["data"]>["paired_differences"];
}) {
  if (!pairs) return null;
  return (
    <>
      <h2>Paired task outcomes</h2>
      {Object.entries(pairs).map(([pairKey, diffs]) => {
        const [left, right] = pairKey.split("|");
        return (
          <div key={pairKey}>
            <h3>
              {left} vs {right}
            </h3>
            <table>
              <caption>Per-task rate difference ({left} minus {right})</caption>
              <thead>
                <tr>
                  <th scope="col">Task</th>
                  <th scope="col">{left}</th>
                  <th scope="col">{right}</th>
                  <th scope="col">Difference</th>
                </tr>
              </thead>
              <tbody>
                {diffs.map((diff) => (
                  <tr key={diff.task_id}>
                    <th scope="row">{diff.task_id}</th>
                    <td className="tabular-nums">{formatRate(diff.left_rate)}</td>
                    <td className="tabular-nums">{formatRate(diff.right_rate)}</td>
                    <td className="tabular-nums">{formatPercentagePointDifference(diff.difference)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      })}
    </>
  );
}
