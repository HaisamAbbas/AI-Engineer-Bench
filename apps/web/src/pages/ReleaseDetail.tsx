import { Link, useParams } from "react-router-dom";
import { usePublicationResults } from "../api/hooks";
import { Loading, ErrorState } from "../components/QueryStates";
import { formatUtc } from "../lib/format";

/** "/releases/:id" - frozen task list, cohort, dates, changelog. Backed by
 * the same publication resource /results serves (this codebase's "release"
 * and "publication" are the same underlying row - see supersedes_id below),
 * framed here as a release-level view: which tasks were in the frozen
 * cohort, cohort/evaluation-date metadata, and what it supersedes (the
 * changelog). */
export function ReleaseDetail() {
  const { publicationId } = useParams();
  const results = usePublicationResults(publicationId);

  if (results.isPending) return <Loading label="release" />;
  if (results.isError) return <ErrorState error={results.error} onRetry={() => results.refetch()} />;

  const data = results.data;
  // The frozen task list comes from the campaign's own manifest
  // (`campaign.resolved["tasks"]`, exposed as `frozen_tasks`), NOT inferred
  // from which tasks happen to have a snapshot.per_task cell - a planned
  // task with zero observations must not silently disappear (review finding
  // #3; spec section 28's incomplete-coverage reporting must preserve it).
  const created = formatUtc(data.created_at);
  const observedTaskIds = new Set(Object.keys(data.snapshot.per_task).map((key) => key.slice(0, key.lastIndexOf(":"))));

  return (
    <section>
      <h1>Release {data.id}</h1>
      <dl>
        <dt>Status</dt>
        <dd>{data.status}</dd>
        <dt>Campaign</dt>
        <dd>{data.campaign_id}</dd>
        <dt>Cohort digest</dt>
        <dd>{data.cohort_digest ?? "Unknown"}</dd>
        <dt>Protocol scoring digest</dt>
        <dd>{data.protocol_scoring_digest ?? "Unknown"}</dd>
        <dt>Evaluation window</dt>
        <dd>
          {data.evaluation_started_at && data.evaluation_completed_at
            ? `${formatUtc(data.evaluation_started_at).display} to ${formatUtc(data.evaluation_completed_at).display}`
            : "Unknown"}
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
        {/* Publication time, not an evaluation date range - the campaign's
         * own actual run-window dates are not tracked yet (review finding
         * #4, second pass). */}
        <dt>Published</dt>
        <dd title={created.localTitle}>{created.display}</dd>
      </dl>
      {data.status === "withdrawn" && (
        <p role="alert">This release has been withdrawn; it remains addressable but is not canonical.</p>
      )}
      {data.status === "superseded" && (
        <p role="alert">This release has been superseded by a later, corrected snapshot.</p>
      )}
      <h2>Frozen task list</h2>
      {data.frozen_tasks.length === 0 ? (
        <p>No tasks are recorded in this release's frozen manifest.</p>
      ) : (
        <ul>
          {[...data.frozen_tasks]
            .sort((a, b) => a.slug.localeCompare(b.slug))
            .map((task) => (
              <li key={task.slug}>
                {task.slug} ({task.version}) - {task.category}
                {!observedTaskIds.has(task.slug) && <span className="badge badge-incomplete"> no observations</span>}
              </li>
            ))}
        </ul>
      )}
      <h2>Changelog</h2>
      {data.supersedes_id ? (
        <p>
          Supersedes <Link to={`/releases/${data.supersedes_id}`}>release {data.supersedes_id}</Link>. See{" "}
          <Link to="/corrections">Corrections</Link> for the full correction history.
        </p>
      ) : (
        <p>This release does not supersede an earlier one.</p>
      )}
      <p>
        <Link to={`/results?publication=${data.id}`}>View results for this release</Link>
      </p>
    </section>
  );
}
