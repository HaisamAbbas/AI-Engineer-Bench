import { Link, useParams } from "react-router-dom";
import { useEntrantRevisionBySlug, useEntrantResults } from "../api/hooks";
import { Loading, ErrorState, EmptyState } from "../components/QueryStates";
import { formatRate, formatUtc } from "../lib/format";

/** "/entrants/:slug" - exact version, model(s), settings, results by
 * release. Looked up by slug (matching aieb_analysis snapshots' per_entrant
 * keys), resolving to the most recent revision for that slug - a specific
 * historical result's exact configuration can differ once a slug has more
 * than one revision, since the snapshot itself carries no per-result
 * revision pointer; that is a disclosed limitation, not a silent guess. */
export function EntrantProfile() {
  const { slug } = useParams();
  const entrant = useEntrantRevisionBySlug(slug);
  const results = useEntrantResults(slug);

  if (entrant.isPending) return <Loading label="entrant" />;
  if (entrant.isError) return <ErrorState error={entrant.error} onRetry={() => entrant.refetch()} />;

  const manifest = entrant.data.manifest;

  return (
    <section>
      <h1>{manifest.id}</h1>
      <p role="note">
        Showing the most recent revision known for this entrant slug; a specific historical result below may have
        used a different revision - the snapshot does not record which.
      </p>
      <dl>
        <dt>Track</dt>
        <dd>{manifest.track}</dd>
        <dt>Agent implementation</dt>
        <dd>{manifest.agent_implementation}</dd>
        <dt>Agent version</dt>
        <dd>{manifest.agent_version}</dd>
        <dt>Model</dt>
        <dd>
          {manifest.engineer_model.reported_model ?? manifest.engineer_model.requested_model} (
          {manifest.engineer_model.provider_class})
        </dd>
        <dt>Capabilities</dt>
        <dd>{manifest.capabilities.join(", ")}</dd>
        <dt>Credential reference</dt>
        <dd>{manifest.credential_ref_type}</dd>
      </dl>
      <h2>Results by release</h2>
      {results.isPending && <Loading label="results" />}
      {results.isError && <ErrorState error={results.error} onRetry={() => results.refetch()} />}
      {results.isSuccess && results.data.length === 0 && (
        <EmptyState title="This entrant does not appear in any published release yet." />
      )}
      {results.isSuccess && results.data.length > 0 && (
        <table>
          <caption>Publications this entrant appears in</caption>
          <thead>
            <tr>
              <th scope="col">Release</th>
              <th scope="col">Status</th>
              <th scope="col">Rate</th>
              <th scope="col">Published</th>
            </tr>
          </thead>
          <tbody>
            {results.data.map((entry) => {
              const created = formatUtc(entry.created_at);
              return (
                <tr key={entry.publication_id}>
                  <th scope="row">
                    <Link to={`/releases/${entry.publication_id}`}>{entry.publication_id}</Link>
                  </th>
                  <td>{entry.status}</td>
                  <td className="tabular-nums">{formatRate(entry.aggregate_rate)}</td>
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
