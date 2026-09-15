import { Link } from "react-router-dom";
import { useReleases } from "../api/hooks";
import { Loading, ErrorState, EmptyState } from "../components/QueryStates";
import { formatUtc } from "../lib/format";
import type { PublicationSummary } from "../api/types";

/** "/" - default page selects the newest non-withdrawn publication (spec
 * section 4). /v1/releases returns status=published rows newest-first, so
 * the newest is simply the first page's first item. */
export function Home() {
  const releases = useReleases();

  return (
    <section>
      <h1>AI Engineer Bench</h1>
      <p>
        A reproducible benchmark for AI coding agents repairing real application defects under a fixed, disclosed
        protocol. Scores are scoped to the evaluated suite/track/profile - see{" "}
        <Link to="/methodology">Methodology</Link> before drawing conclusions.
      </p>

      {releases.isPending && <Loading label="the latest publication" />}
      {releases.isError && <ErrorState error={releases.error} onRetry={() => releases.refetch()} />}
      {releases.isSuccess && <LatestPublication items={releases.data.items} />}

      <p>
        <Link to="/results">View results</Link> · <Link to="/docs">Run locally</Link>
      </p>
    </section>
  );
}

function LatestPublication({ items }: { items: PublicationSummary[] }) {
  if (items.length === 0) {
    return (
      <EmptyState title="No publication has been released yet.">
        <p>
          This is a development-phase preview. Once a campaign is published, its latest non-withdrawn snapshot will
          appear here. In the meantime, see <Link to="/docs">Run locally</Link> to evaluate an entrant yourself.
        </p>
      </EmptyState>
    );
  }
  const latest = items[0];
  const created = formatUtc(latest.created_at);
  return (
    <div className="latest-publication">
      <p>Latest publication:</p>
      <p title={created.localTitle}>{created.display}</p>
      <Link to={`/releases/${latest.id}`}>View release {latest.id}</Link>
    </div>
  );
}
