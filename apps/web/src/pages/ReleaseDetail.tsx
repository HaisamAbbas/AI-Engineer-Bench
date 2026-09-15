import { Link, useParams } from "react-router-dom";
import { usePublicationResults } from "../api/hooks";
import { Loading, ErrorState } from "../components/QueryStates";

interface Snapshot {
  per_task: Record<string, unknown>;
}

/** "/releases/:id" - frozen task list, cohort, dates, changelog. Backed by
 * the same publication resource /results serves (this codebase's "release"
 * and "publication" are the same underlying row - see supersedes_id below),
 * framed here as a release-level view: which tasks were in the frozen
 * cohort, and what it supersedes (the changelog). */
export function ReleaseDetail() {
  const { publicationId } = useParams();
  const results = usePublicationResults(publicationId);

  if (results.isPending) return <Loading label="release" />;
  if (results.isError) return <ErrorState error={results.error} onRetry={() => results.refetch()} />;

  const data = results.data as {
    id: string;
    campaign_id: string;
    status: string;
    supersedes_id: string | null;
    snapshot: Snapshot;
  };
  const taskIds = [...new Set(Object.keys(data.snapshot.per_task ?? {}).map((key) => key.split(":")[0]))].sort();

  return (
    <section>
      <h1>Release {data.id}</h1>
      <dl>
        <dt>Status</dt>
        <dd>{data.status}</dd>
        <dt>Campaign</dt>
        <dd>{data.campaign_id}</dd>
      </dl>
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
          Supersedes <Link to={`/releases/${data.supersedes_id}`}>release {data.supersedes_id}</Link>.
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
