import { Link, useParams } from "react-router-dom";
import { useState } from "react";
import { useCampaignProgress, useInvalidAttempts } from "../api/hooks";
import { useInvalidAttemptEvidence, useReviewInvalidAttempt, type InvalidityReviewInput } from "../api/invalidityHooks";
import { EmptyState, ErrorState, Loading } from "../components/QueryStates";

export function CampaignProgress() {
  const { campaignId } = useParams<{ campaignId: string }>();
  const [selected, setSelected] = useState<string>();
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
      <h2>Invalid-attempt review</h2>
      <p>Inspect the exact historical attempt and, as a reviewer or administrator, record an append-only agree/disagree decision with a rationale. Reviews never change the recorded outcome. Run evidence describes the latest attempt for a trial, not necessarily the historical attempt listed below.</p>
      {invalid.isPending && <Loading label="invalid attempts" />}
      {invalid.isError && <ErrorState error={invalid.error} onRetry={() => void invalid.refetch()} />}
      {invalid.data && (invalid.data.length ? <div className="table-scroll"><table><caption>Invalid and cancelled attempts</caption><thead><tr><th>Review</th><th>Attempt ID</th><th>Number</th><th>Terminal status</th><th>Trial evidence</th></tr></thead><tbody>
        {invalid.data.map(row => <tr key={row.attempt_id}>
          <td><input type="radio" name="invalid-attempt" aria-label={`Review attempt ${row.attempt_number}`} checked={selected === row.attempt_id} onChange={() => setSelected(row.attempt_id)} /></td>
          <td>{row.attempt_id}</td><td>{row.attempt_number}</td><td>{row.terminal_status}</td>
          <td><Link to={`/runs/${row.trial_id}`}>{row.trial_id}</Link></td>
        </tr>)}
      </tbody></table></div> : <EmptyState title="No invalid or cancelled attempts reported." />)}
      {selected && campaignId && <InvalidityReviewPanel key={`${campaignId}:${selected}`} campaignId={campaignId} attemptId={selected} />}
    </>}
  </section>;
}
function Counts({ title, rows }: { title: string; rows: { state: string; count: number }[] }) {
  return rows.length ? <table><caption>{title}</caption><thead><tr><th>State</th><th>Count</th></tr></thead><tbody>{rows.map(row => <tr key={row.state}><td>{row.state}</td><td>{row.count}</td></tr>)}</tbody></table> : <EmptyState title={`${title}: none reported.`} />;
}

function InvalidityReviewPanel({ campaignId, attemptId }: { campaignId: string; attemptId: string }) {
  const evidence = useInvalidAttemptEvidence(campaignId, attemptId);
  const review = useReviewInvalidAttempt(campaignId, attemptId);
  const [rationale, setRationale] = useState("");
  if (evidence.isPending) return <Loading label="invalidity evidence" />;
  if (evidence.isError || !evidence.data) return <ErrorState error={evidence.error} onRetry={() => void evidence.refetch()} />;
  function submit(event: React.FormEvent, decision: InvalidityReviewInput["decision"]) {
    event.preventDefault();
    if (!rationale.trim() || review.isPending) return;
    review.mutate({ body: { decision, rationale: rationale.trim() } },
      { onSuccess: () => setRationale("") });
  }
  return <section aria-label="Invalidity review evidence">
    <h3>Attempt {evidence.data.attempt_number} — {evidence.data.terminal_status}</h3>
    <dl><dt>Campaign</dt><dd>{campaignId}</dd><dt>Trial</dt><dd>{evidence.data.trial_id}</dd>
      <dt>Attempt</dt><dd>{attemptId}</dd><dt>Phase</dt><dd>{evidence.data.phase}</dd></dl>
    <h4>Recorded lifecycle events</h4>
    {evidence.data.events.length
      ? <ul>{evidence.data.events.map(event => <li key={event.sequence}>{event.event_type} — {new Date(event.created_at).toISOString()}</li>)}</ul>
      : <p>No lifecycle events recorded for this attempt.</p>}
    <h4>Recorded reviews ({evidence.data.reviews.length})</h4>
    {evidence.data.reviews.length
      ? <table><caption>Append-only invalidity reviews</caption><thead><tr><th>Decision</th><th>Rationale</th><th>Recorded</th></tr></thead>
          <tbody>{evidence.data.reviews.map(r => <tr key={r.id}><td>{r.decision}</td><td>{r.rationale}</td><td>{new Date(r.created_at).toISOString()}</td></tr>)}</tbody></table>
      : <EmptyState title="No review decisions recorded yet." />}
    {review.error && <ErrorState error={review.error} onRetry={review.reset} />}
    {evidence.data.can_review
      ? <form aria-label="Record invalidity review" onSubmit={e => submit(e, "reject")}>
          <p>Record whether you agree with this classification. Decisions are append-only; a later review can disagree, and the recorded outcome never changes.</p>
          <label>Rationale<textarea required value={rationale} onChange={e => setRationale(e.target.value)} /></label>
          <button type="button" disabled={review.isPending || !rationale.trim()} onClick={e => submit(e, "reject")}>Disagree with classification</button>
          <button type="button" disabled={review.isPending || !rationale.trim()} onClick={e => submit(e, "approve")}>Agree with classification</button>
        </form>
      : <p>Reviewer or administrator role required to record a review decision.</p>}
  </section>;
}
