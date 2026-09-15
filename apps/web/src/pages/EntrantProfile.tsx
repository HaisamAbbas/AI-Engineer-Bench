import { useParams } from "react-router-dom";
import { useEntrantRevision } from "../api/hooks";
import { Loading, ErrorState } from "../components/QueryStates";

interface EntrantManifest {
  id: string;
  track: string;
  agent_implementation: string;
  agent_version: string;
  engineer_model: { provider_class: string; requested_model: string; reported_model: string | null };
  capabilities: string[];
  credential_ref_type: string;
}

/** "/entrants/:revision" - exact version, model(s), settings, results by
 * release. "Results by release" needs cross-referencing every publication
 * that includes this entrant, which has no dedicated backend query yet
 * (would require scanning every publication's snapshot) - shown as a
 * disclosed limitation rather than fabricated. */
export function EntrantProfile() {
  const { revision } = useParams();
  const entrant = useEntrantRevision(revision);

  if (entrant.isPending) return <Loading label="entrant" />;
  if (entrant.isError) return <ErrorState error={entrant.error} onRetry={() => entrant.refetch()} />;

  const manifest = entrant.data.manifest as EntrantManifest;

  return (
    <section>
      <h1>{manifest.id}</h1>
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
      <p>
        Cross-referencing every publication this entrant appears in is not yet available from a single query - open{" "}
        <a href="/results">Results</a> and select a specific publication to see this entrant's rate there.
      </p>
    </section>
  );
}
