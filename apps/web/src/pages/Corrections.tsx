import { Link, useSearchParams } from "react-router-dom";
import { useCorrections } from "../api/hooks";
import { Loading, ErrorState, EmptyState } from "../components/QueryStates";
import { formatUtc } from "../lib/format";

/** Append-only correction history with separately retained withdrawal reasons. */
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
        <div className="table-scroll" role="region" aria-label="Correction history" tabIndex={0}>
        <table>
          <caption>Append-only correction history (newest first). Correction and withdrawal reasons are recorded separately.</caption>
          <thead>
            <tr>
              <th scope="col">Publication (after)</th>
              <th scope="col">Status</th>
              <th scope="col">Supersedes (before)</th>
              <th scope="col">Publication class</th>
              <th scope="col">Correction reason</th>
              <th scope="col">Withdrawal reason</th>
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
                  <td>{item.publication_class === "non_ranked" ? "Non-ranked" : "Ranked"}</td>
                  <td>{item.reason ?? "Not recorded"}</td>
                  <td>{item.withdrawal_reason ?? (item.status === "withdrawn" ? "Not recorded" : "Not withdrawn")}</td>
                  <td title={created.localTitle}>{created.display}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        </div>
      )}
      {corrections.data?.next_cursor && (
        <button type="button" onClick={goToNextPage}>
          Next page
        </button>
      )}
    </section>
  );
}
