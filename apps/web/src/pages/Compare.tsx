import { useSearchParams } from "react-router-dom";
import { usePublicationResults, useComparison } from "../api/hooks";
import { Loading, ErrorState, EmptyState } from "../components/QueryStates";
import { formatRate } from "../lib/format";

/** "/compare" - up to 4 entrants, side by side, within one publication's
 * frozen cohort (they share the same plan by construction, so they are
 * always paired-comparable at this scope). Comparing entrants ACROSS two
 * different releases/cohorts - spec's "cross-release comparison shows
 * separate panels with a non-comparable label" - needs a backend endpoint
 * that accepts two publication IDs, which does not exist yet; this page is
 * honestly scoped to same-publication comparison until that exists. */
export function Compare() {
  const [params, setParams] = useSearchParams();
  const publicationId = params.get("publication") ?? undefined;
  const entrantIds = (params.get("entrants") ?? "").split(",").filter(Boolean);

  const results = usePublicationResults(publicationId);
  const comparison = useComparison(publicationId, entrantIds);

  if (!publicationId || entrantIds.length < 2) {
    return (
      <section>
        <h1>Compare</h1>
        <EmptyState title="Select 2 to 4 entrants to compare.">
          <p>Go to Results, select entrants with the checkboxes, then choose "Compare selected entrants".</p>
        </EmptyState>
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

  function removeEntrant(id: string) {
    const next = entrantIds.filter((e) => e !== id);
    setParams({ publication: publicationId!, entrants: next.join(",") });
  }

  return (
    <section>
      <h1>Compare</h1>
      {results.isPending && <Loading label="publication" />}
      {results.isError && <ErrorState error={results.error} onRetry={() => results.refetch()} />}
      {comparison.isPending && <Loading label="comparison" />}
      {comparison.isError && <ErrorState error={comparison.error} onRetry={() => comparison.refetch()} />}
      {comparison.isSuccess && (
        <div className="compare-panels">
          {!comparison.data.cohort_comparable && (
            <p role="alert">These entrants are not cohort-comparable; paired statistics are disabled.</p>
          )}
          <div className="compare-grid">
            {entrantIds.map((entrantId) => {
              const entrants = comparison.data.entrants as unknown as Record<
                string,
                { eligible: true; aggregate: number | null } | { eligible: false; reason: string }
              >;
              const entry = entrants[entrantId];
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
        </div>
      )}
      <p>
        <button
          type="button"
          onClick={() => navigator.clipboard?.writeText(window.location.href)}
        >
          Copy URL
        </button>
      </p>
    </section>
  );
}
