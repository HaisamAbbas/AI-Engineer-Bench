import { Link, useParams } from "react-router-dom";
import { useCampaignProgress, useInvalidAttempts } from "../api/hooks";
import { EmptyState, ErrorState, Loading } from "../components/QueryStates";

export function CampaignProgress() {
  const { campaignId } = useParams<{ campaignId: string }>();
  const progress = useCampaignProgress(campaignId);
  const invalid = useInvalidAttempts(campaignId);
  return <section className="admin-page"><h1>Campaign progress</h1>
    {!campaignId ? <p>A campaign ID is required.</p> : <>
      <p><Link to={`/admin/campaigns/${campaignId}`}>Campaign admin</Link> · <Link to={`/admin/campaigns/${campaignId}/publications`}>Publication review</Link></p>
      <p>Server counts refresh every five seconds while this page is active. Observed trials are not completed trials.</p>
      <button disabled={progress.isFetching || invalid.isFetching} onClick={() => { void progress.refetch(); void invalid.refetch(); }}>Refresh progress</button>
      {progress.isPending && <Loading label="campaign progress" />}
      {progress.isError && <ErrorState error={progress.error} onRetry={() => void progress.refetch()} />}
      {progress.data && <><dl><dt>Server state</dt><dd>{progress.data.state}</dd><dt>Planned trials</dt><dd>{progress.data.planned_trials}</dd><dt>Observed trials</dt><dd>{progress.data.observed_trials}</dd></dl>
        <Counts title="Attempts by phase" rows={progress.data.attempts_by_phase} />
        <Counts title="Attempts by terminal status" rows={progress.data.attempts_by_terminal_status} />
        <Counts title="Work items by state" rows={progress.data.work_items_by_state} /></>}
      <h2>Invalid-attempt inspection</h2>
      <p>The API exposes invalid and cancelled attempts but has no review-decision write endpoint. Inspect evidence; no approval or rejection is recorded here. Run evidence currently describes the latest attempt for a trial, not necessarily the historical attempt listed below.</p>
      {invalid.isPending && <Loading label="invalid attempts" />}
      {invalid.isError && <ErrorState error={invalid.error} onRetry={() => void invalid.refetch()} />}
      {invalid.data && (invalid.data.length ? <div className="table-scroll"><table><caption>Invalid and cancelled attempts</caption><thead><tr><th>Attempt ID</th><th>Number</th><th>Terminal status</th><th>Trial evidence</th></tr></thead><tbody>
        {invalid.data.map(row => <tr key={row.attempt_id}><td>{row.attempt_id}</td><td>{row.attempt_number}</td><td>{row.terminal_status}</td><td><Link to={`/runs/${row.trial_id}`}>{row.trial_id}</Link></td></tr>)}
      </tbody></table></div> : <EmptyState title="No invalid or cancelled attempts reported." />)}
    </>}
  </section>;
}
function Counts({ title, rows }: { title: string; rows: { state: string; count: number }[] }) {
  return rows.length ? <table><caption>{title}</caption><thead><tr><th>State</th><th>Count</th></tr></thead><tbody>{rows.map(row => <tr key={row.state}><td>{row.state}</td><td>{row.count}</td></tr>)}</tbody></table> : <EmptyState title={`${title}: none reported.`} />;
}
