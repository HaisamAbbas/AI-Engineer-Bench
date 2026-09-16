/** Versioned protocol records with the stable explanations of the measures
 * and inclusion rules shown alongside each published revision. */
import { useNavigate, useParams } from "react-router-dom";
import { useMethodologyRevision, useMethodologyRevisions } from "../api/hooks";
import { ErrorState, Loading } from "../components/QueryStates";

export function Methodology() {
  const { version } = useParams();
  const navigate = useNavigate();
  const revisions = useMethodologyRevisions();
  const effectiveVersion = version ?? revisions.data?.[0]?.version;
  const selected = useMethodologyRevision(effectiveVersion);

  if (revisions.isPending) return <Loading label="methodology revisions" />;
  if (revisions.isError) return <ErrorState error={revisions.error} onRetry={() => revisions.refetch()} />;

  function downloadProtocol() {
    if (!selected.data) return;
    const blob = new Blob([JSON.stringify(selected.data.manifest, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${selected.data.version}-methodology.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  if (effectiveVersion && selected.isPending) return <Loading label="methodology revision" />;
  if (effectiveVersion && selected.isError) return <ErrorState error={selected.error} onRetry={() => selected.refetch()} />;
  const protocol = selected.data?.manifest;

  return (
    <section>
      <h1>Methodology</h1>
      {revisions.data.length > 0 ? (
        <>
          <label htmlFor="methodology-version">Protocol version</label>
          <select
            id="methodology-version"
            value={effectiveVersion}
            onChange={(event) => navigate(`/methodology/${encodeURIComponent(event.target.value)}`)}
          >
            {revisions.data.map((item) => (
              <option key={item.version} value={item.version}>
                {item.version}
              </option>
            ))}
          </select>
          {protocol && (
            <>
              <dl>
                <dt>Scoring digest</dt>
                <dd><code>{protocol.scoring_digest}</code></dd>
                <dt>Maximum replacements</dt>
                <dd>{protocol.max_replacements}</dd>
                <dt>Required trace coverage</dt>
                <dd>{protocol.required_trace_coverage ? "Required" : "Not required"}</dd>
                <dt>Hard cost ranking</dt>
                <dd>{protocol.hard_cost_ranking ? "Enabled" : "Disabled"}</dd>
              </dl>
              <button type="button" onClick={downloadProtocol}>Download this protocol revision</button>
            </>
          )}
        </>
      ) : (
        <p role="status">No frozen campaign has registered a protocol revision yet. The guide below describes the current development methodology.</p>
      )}
      <div id="protocol-content">
        <h2>What is measured</h2>
        <p>
          Entrants (AI coding agents) repair a fixed, disclosed defect in a real application, under a monotonic
          engineering clock, with role-separated cost accounting (engineer / development-application / verifier).
          A trial is scored PASS, FAIL, or CONTRACT_VIOLATION by a trusted verifier the entrant never controls;
          infrastructure-invalid and cancelled attempts are not scored outcomes.
        </p>
        <h2>Metrics</h2>
        <ul>
          <li>Per-task successes over trials with a Wilson 95% confidence interval.</li>
          <li>All-k repeatability and the partial-pass-power estimator.</li>
          <li>Fixed-weight suite rate, per-entrant rate, and per-category rates.</li>
          <li>Cost per resolution, verifier cost, and total campaign cost reported separately.</li>
          <li>Median successful engineering time, deadline rate, and infrastructure attrition.</li>
        </ul>
        <h2>Inclusion rules</h2>
        <ul>
          <li>A missing planned trial blocks a canonical complete ranking.</li>
          <li>Zero successes gives an undefined cost per resolution, never a fabricated number.</li>
          <li>Missing cost accounting produces an unavailable estimate, not an assumed zero.</li>
        </ul>
        <h2>Limitations</h2>
        <ul>
          <li>Project/family-clustered analysis is exploratory with fewer than six underlying projects.</li>
          <li>No p95 or similar tail statistic is displayed below 100 eligible observations.</li>
          <li>Statistical significance is not automatically practical importance; campaigns name a minimum effect of interest before measurement.</li>
        </ul>
      </div>
    </section>
  );
}
