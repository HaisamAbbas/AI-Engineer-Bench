import { Link, useSearchParams } from "react-router-dom";
import { useReleases } from "../api/hooks";
import { Loading, ErrorState, EmptyState } from "../components/QueryStates";
import { formatUtc } from "../lib/format";

export function ReleasesList() {
  const [params, setParams] = useSearchParams();
  const cursor = params.get("cursor") ?? undefined;
  const releases = useReleases(cursor);

  if (releases.isPending) return <Loading label="releases" />;
  if (releases.isError) return <ErrorState error={releases.error} onRetry={() => releases.refetch()} />;

  const items = releases.data.items;

  function goToNextPage() {
    if (releases.data?.next_cursor) setParams({ cursor: releases.data.next_cursor });
  }

  return (
    <section>
      <h1>Releases</h1>
      {items.length === 0 && <EmptyState title="No releases have been published yet." />}
      {items.length > 0 && (
      <div className="table-scroll" role="region" aria-label="Published releases" tabIndex={0}>
      <table>
          <caption>Published releases (newest first)</caption>
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
      </div>
      )}
      {releases.data?.next_cursor && (
        <button type="button" onClick={goToNextPage}>
          Next page
        </button>
      )}
    </section>
  );
}
