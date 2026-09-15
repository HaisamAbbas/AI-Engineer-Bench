import { Link } from "react-router-dom";
import { useReleases } from "../api/hooks";
import { Loading, ErrorState, EmptyState } from "../components/QueryStates";
import { formatUtc } from "../lib/format";

interface PublicationSummary {
  id: string;
  campaign_id: string;
  snapshot_digest: string;
  status: string;
  created_at: string;
}

export function ReleasesList() {
  const releases = useReleases();

  if (releases.isPending) return <Loading label="releases" />;
  if (releases.isError) return <ErrorState error={releases.error} onRetry={() => releases.refetch()} />;

  const items = releases.data.items as unknown as PublicationSummary[];

  return (
    <section>
      <h1>Releases</h1>
      {items.length === 0 && <EmptyState title="No releases have been published yet." />}
      {items.length > 0 && (
        <table>
          <caption>Published releases</caption>
          <thead>
            <tr>
              <th scope="col">Release</th>
              <th scope="col">Status</th>
              <th scope="col">Published</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => {
              const created = formatUtc(item.created_at);
              return (
                <tr key={item.id}>
                  <th scope="row">
                    <Link to={`/releases/${item.id}`}>{item.id}</Link>
                  </th>
                  <td>{item.status}</td>
                  <td title={created.localTitle}>{created.display}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </section>
  );
}
