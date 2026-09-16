import { Link, useSearchParams } from "react-router-dom";
import { useCorrections } from "../api/hooks";
import { Loading, ErrorState, EmptyState } from "../components/QueryStates";
import { formatUtc } from "../lib/format";

/** "/corrections" - append-only corrections and reasons. There is no
 * free-text "reason" field on publication yet, so this shows that a
 * correction happened (a supersession or withdrawal) and what it changed
 * (before/after publication), not why - a disclosed gap, not a fabricated
 * reason. */
export function Corrections() {
  const [params, setParams] = useSearchParams();
  const cursor = params.get("cursor") ?? undefined;
  const corrections = useCorrections(cursor);

  if (corrections.isPending) return <Loading label="corrections" />;
  if (corrections.isError) return <ErrorState error={corrections.error} onRetry={() => corrections.refetch()} />;

  const items = corrections.data.items;

  function goToNextPage() {
    if (corrections.data?.next_cursor) setParams({ cursor: corrections.data.next_cursor });
  }

  return (
    <section>
      <h1>Corrections</h1>
      {items.length === 0 ? (
        <EmptyState title="No corrections have been made." />
      ) : (
        <table tabIndex={0}>
          <caption>Append-only correction history (newest first). Reasons are not yet recorded - see release changelogs for what changed.</caption>
          <thead>
            <tr>
              <th scope="col">Publication</th>
              <th scope="col">Status</th>
              <th scope="col">Supersedes</th>
              <th scope="col">Date</th>
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
                  <td>
                    {item.supersedes_id ? (
                      <Link to={`/releases/${item.supersedes_id}`}>{item.supersedes_id}</Link>
                    ) : (
                      "None"
                    )}
                  </td>
                  <td title={created.localTitle}>{created.display}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      {corrections.data?.next_cursor && (
        <button type="button" onClick={goToNextPage}>
          Next page
        </button>
      )}
    </section>
  );
}
