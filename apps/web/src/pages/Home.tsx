import { Link } from "react-router-dom";
import { useReleases } from "../api/hooks";
import { Loading, ErrorState, EmptyState } from "../components/QueryStates";
import { formatUtc } from "../lib/format";

/** "/" - default page selects the newest non-withdrawn publication (spec
 * section 4). /v1/releases already returns only status=published rows,
 * ordered oldest-first for stable pagination, so the newest is the last
 * page's last item - for a first release with few rows this is just the
 * last item of the one page fetched. */
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
      {releases.isSuccess && (
        <LatestPublication items={releases.data.items as unknown as PublicationSummary[]} />
      )}

      <p>
        <Link to="/results">View results</Link> · <Link to="/docs">Run locally</Link>
      </p>
    </section>
  );
}

interface PublicationSummary {
  id: string;
  campaign_id: string;
  snapshot_digest: string;
  status: string;
  created_at: string;
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
  const latest = items[items.length - 1];
  const created = formatUtc(latest.created_at);
  return (
    <div className="latest-publication">
      <p>Latest publication:</p>
      <p title={created.localTitle}>{created.display}</p>
      <Link to={`/releases/${latest.id}`}>View release {latest.id}</Link>
    </div>
  );
}
