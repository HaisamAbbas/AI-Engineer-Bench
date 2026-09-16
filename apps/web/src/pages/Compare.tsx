import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { usePublicationResults, useComparison, usePublicationEntrantConfiguration } from "../api/hooks";
import { Loading, ErrorState, EmptyState } from "../components/QueryStates";
import { formatRate, formatPercentagePointDifference, formatUsd, formatSeconds, formatCount } from "../lib/format";

/** "/compare" - up to 4 entrants side by side: exact configuration (agent
 * version, model, capabilities), per-entrant cost/time/coverage (the same
 * typed per-entrant analysis fields the Results table shows, not merely an
 * aggregate rate - review finding #4), and paired task outcomes ONLY when
 * every entrant comes from the SAME publication. An entrant can be pinned
 * to a specific OTHER publication via `entrant_publication_ids` (same order
 * as `entrant_ids`, empty string falling back to the shared `publication`)
 * for a genuine cross-release comparison - but spec journey 6.1 is
 * unconditional: "A cross-release comparison shows separate panels with a
 * non-comparable label, never a calculated winner." Comparability is
 * therefore never based on `cohort_digest` matching across publications
 * (review finding #1) - it is entirely "are these entrants in the same
 * publication or not," decided server-side (`GET /v1/comparisons`). */
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

  // Remove by POSITION, not by slug: the same slug can appear twice (agent-a
  // from two releases), so filtering by slug would drop the wrong panel
  // (review finding #4, third pass).
  function removeEntrantAt(index: number) {
    const nextIds = entrantIds.filter((_, i) => i !== index);
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
            {comparison.data.entrants.map((entry, index) => (
              <EntrantPanel
                key={`${entry.entrant_id}::${entry.publication_id}::${index}`}
                entry={entry}
                onRemove={() => removeEntrantAt(index)}
              />
            ))}
          </div>
          {comparison.data.cohort_comparable && comparison.data.task_rate_deltas && (
            <TaskRateDeltas pairs={comparison.data.task_rate_deltas} />
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

/** One entrant's side-by-side panel (spec journey 6.1 / section 5: "Side-by-side
 * configurations, paired task outcomes, cost/time"). Fetches this entrant's own
 * configuration (agent version, model, capabilities) and, from its own
 * publication's snapshot, the same per-entrant cost/time/coverage numbers the
 * Results table shows - not merely an aggregate rate. */
function EntrantPanel({
  entry,
  onRemove,
}: {
  entry: NonNullable<ReturnType<typeof useComparison>["data"]>["entrants"][number];
  onRemove: () => void;
}) {
  const entrantId = entry.entrant_id;
  const publicationId = entry.publication_id;
  // The EXACT configuration THIS publication's frozen campaign used - never
  // useEntrantRevisionBySlug, which resolves whichever revision is newest
  // right now and could silently mismatch a historical/cross-release panel's
  // own metrics (review finding #2, second pass). Both the config and the
  // metrics are keyed to `entry.publication_id`, so two panels for the same
  // slug from different releases never cross-contaminate (review finding #4,
  // third pass).
  const configuration = usePublicationEntrantConfiguration(publicationId, entrantId);
  const results = usePublicationResults(publicationId);
  const snapshot = results.data?.snapshot;
  // Total tasks come from the frozen plan (data.frozen_tasks), never a
  // per-entrant count derived from observed cells; resolved is a genuine 0
  // for an unobserved entrant but "Unknown" (null) for a snapshot predating
  // the field (review findings #1/#3, third pass).
  const totalTasks = results.data ? (results.data.frozen_tasks.length > 0 ? results.data.frozen_tasks.length : null) : null;
  const resolvedField = snapshot?.per_entrant_resolved_tasks;
  const resolvedTasks = resolvedField == null ? null : resolvedField[entrantId] ?? 0;

  return (
    <article className="compare-panel">
      <h2>{entrantId}</h2>
      <button type="button" onClick={onRemove}>
        Remove
      </button>
      {configuration.isSuccess && (
        <dl>
          <dt>Version</dt>
          <dd>{configuration.data.manifest.agent_version}</dd>
          <dt>Model</dt>
          <dd>
            {configuration.data.manifest.engineer_model.reported_model ?? configuration.data.manifest.engineer_model.requested_model}
          </dd>
          <dt>Capabilities</dt>
          <dd>{configuration.data.manifest.capabilities.join(", ")}</dd>
        </dl>
      )}
      {entry.eligible ? (
        <dl>
          <dt>Rate</dt>
          <dd className="tabular-nums">{formatRate(entry.aggregate)}</dd>
          {snapshot && (
            <>
              <dt>Resolved tasks</dt>
              <dd className="tabular-nums">
                {formatCount(resolvedTasks)}/{formatCount(totalTasks)}
              </dd>
              <dt>Cost / resolution</dt>
              <dd className="tabular-nums">{formatUsd(snapshot.per_entrant_cost_per_resolution?.[entrantId] ?? null)}</dd>
              <dt>Median engineering time</dt>
              <dd className="tabular-nums">{formatSeconds(snapshot.per_entrant_median_engineering_seconds?.[entrantId] ?? null)}</dd>
              <dt>Deadline rate</dt>
              <dd className="tabular-nums">{formatRate(snapshot.per_entrant_deadline_rate?.[entrantId] ?? null)}</dd>
            </>
          )}
        </dl>
      ) : (
        <p>Not eligible: {entry.reason}</p>
      )}
    </article>
  );
}

function TaskRateDeltas({
  pairs,
}: {
  pairs: NonNullable<ReturnType<typeof useComparison>["data"]>["task_rate_deltas"];
}) {
  if (!pairs) return null;
  return (
    <>
      {/* Named "per-task rate deltas," never "paired task outcomes" (review
       * finding #5, second pass): two entrants can have rates based on
       * DIFFERENT valid repetition counts within the same publication -
       * subtracting them is a real, honest descriptive comparison, but it
       * is not the repetition-matched statistic "paired" implies. Real
       * paired statistics need raw matched observations, which the
       * persisted snapshot does not retain (disclosed separately). */}
      <h2>Per-task rate deltas</h2>
      <p>
        A rate difference per task between two entrants, computed from each entrant&rsquo;s own aggregated rate for
        that task - not a repetition-matched paired statistic (see the API's own disclosure).
      </p>
      {Object.entries(pairs).map(([pairKey, diffs]) => {
        const [left, right] = pairKey.split("|");
        return (
          <div key={pairKey}>
            <h3>
              {left} vs {right}
            </h3>
      <table tabIndex={0}>
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
