/** "/methodology/:version" - protocol, metrics, inclusion rules, limitations.
 *
 * There is no persisted, queryable ProtocolRevision history yet (the model
 * exists in aieb_core but nothing writes/reads it as a versioned, published
 * document) - this page is real, disclosed static content describing the
 * one protocol version this repository currently implements, not a stub
 * standing in for a versioned API that doesn't exist. When protocol
 * versioning is built, this becomes real per-version content instead. */
const PROTOCOL_VERSION = "aieb.protocol/v1 (development)";

export function Methodology() {
  function downloadProtocol() {
    const blob = new Blob([document.getElementById("protocol-content")?.textContent ?? ""], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "aieb-methodology.txt";
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <section>
      <h1>Methodology</h1>
      <p>
        Protocol version: <strong>{PROTOCOL_VERSION}</strong>
      </p>
      <p role="alert">
        No official campaign has been reviewed and published under this protocol yet. Everything below describes
        the evaluated scope and limitations that will apply once one is.
      </p>
      <button type="button" onClick={downloadProtocol}>
        Download protocol
      </button>
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
          <li>Per-task s/n with a Wilson 95% confidence interval.</li>
          <li>All-k repeatability (every planned repetition passes) and the C(s,k)/C(n,k) partial-pass-power estimator.</li>
          <li>A fixed-weight suite rate, per-entrant rate, and per-category rate (per entrant, never blended across entrants).</li>
          <li>
            Cost per resolution: engineer + development-application cost for scored trials only, divided by
            successful resolutions - infrastructure-invalid and unresolved attempts are excluded from the
            numerator. Verifier cost and total campaign cost (including invalid attempts) are reported separately.
          </li>
          <li>Median successful engineering time, deadline rate, and infrastructure attrition.</li>
        </ul>
        <h2>Inclusion rules</h2>
        <ul>
          <li>A missing planned trial blocks a canonical complete ranking; nothing is silently treated as complete.</li>
          <li>Zero successes gives an explicitly undefined cost per resolution, never a fabricated number.</li>
          <li>Missing cost accounting produces an unavailable estimate, not an assumed zero.</li>
        </ul>
        <h2>Limitations</h2>
        <ul>
          <li>Project/family-clustered analysis is exploratory with fewer than six underlying projects.</li>
          <li>No p95 or similar tail statistic is displayed below 100 eligible observations.</li>
          <li>Statistical significance is not automatically practical importance; a campaign plan names its minimum effect of interest before measurement.</li>
        </ul>
      </div>
    </section>
  );
}
