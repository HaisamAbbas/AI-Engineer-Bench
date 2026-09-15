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
  const taskIds = [...new Set(Object.keys(data.snapshot.per_task).map((key) => key.slice(0, key.lastIndexOf(":"))))].sort();
  const created = formatUtc(data.created_at);

  return (
    <section>
      <h1>Release {data.id}</h1>
      <dl>
        <dt>Status</dt>
        <dd>{data.status}</dd>
        <dt>Campaign</dt>
        <dd>{data.campaign_id}</dd>
        <dt>Cohort</dt>
        <dd>{data.cohort_digest ?? "Unknown"}</dd>
        <dt>Evaluation date</dt>
        <dd title={created.localTitle}>{created.display}</dd>
      </dl>
      {data.status === "withdrawn" && (
        <p role="alert">This release has been withdrawn; it remains addressable but is not canonical.</p>
      )}
      {data.status === "superseded" && (
        <p role="alert">This release has been superseded by a later, corrected snapshot.</p>
      )}
      <h2>Frozen task list</h2>
      {taskIds.length === 0 ? (
        <p>No tasks are recorded in this release's snapshot.</p>
      ) : (
        <ul>
          {taskIds.map((taskId) => (
            <li key={taskId}>{taskId}</li>
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
