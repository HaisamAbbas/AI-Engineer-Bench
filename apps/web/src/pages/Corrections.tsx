import { Link } from "react-router-dom";
import { useCorrections } from "../api/hooks";
import { Loading, ErrorState, EmptyState } from "../components/QueryStates";
import { formatUtc } from "../lib/format";

interface Correction {
  id: string;
  campaign_id: string;
  status: string;
  supersedes_id: string | null;
  created_at: string;
}

/** "/corrections" - append-only corrections and reasons. There is no
 * free-text "reason" field on publication yet, so this shows that a
 * correction happened (a supersession or withdrawal) and what it changed
 * (before/after publication), not why - a disclosed gap, not a fabricated
 * reason. */
export function Corrections() {
  const corrections = useCorrections();

  if (corrections.isPending) return <Loading label="corrections" />;
  if (corrections.isError) return <ErrorState error={corrections.error} onRetry={() => corrections.refetch()} />;

  const items = corrections.data.items as unknown as Correction[];

  return (
    <section>
      <h1>Corrections</h1>
      {items.length === 0 ? (
        <EmptyState title="No corrections have been made." />
      ) : (
        <table>
          <caption>Append-only correction history</caption>
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
    </section>
  );
}
