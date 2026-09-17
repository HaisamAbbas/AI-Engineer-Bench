import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useCampaign, useCampaignWrite, useMatrixPreview } from "../api/hooks";
import type { components } from "../api/schema";
import { EmptyState, ErrorState, Loading } from "../components/QueryStates";
type Schema = components["schemas"];
function parseObject<T>(text: string): T {
  const value: unknown = JSON.parse(text);
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Enter a JSON object.");
  return value as T; // Server validates the complete manifest.
}
export function CampaignAdmin() {
  const { campaignId } = useParams<{ campaignId: string }>();
  return <section className="admin-page"><h1>Campaign admin</h1>
    <p>Operator access required for writes. Use staging fixtures only; public publication is not authorized.</p>
    {campaignId ? <CampaignDetail key={campaignId} id={campaignId} /> : <CampaignChooser />}
  </section>;
}
function CampaignChooser() {
  const navigate = useNavigate();
  const [id, setId] = useState("");
  const [name, setName] = useState("");
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<unknown>();
  const write = useCampaignWrite();
  return <>
    <EmptyState title="Campaign listing is not available from this API.">Open a known campaign ID or create a new draft.</EmptyState>
    <form onSubmit={e => { e.preventDefault(); if (id.trim()) navigate(`/admin/campaigns/${encodeURIComponent(id.trim())}`); }}>
      <label>Campaign ID<input required value={id} onChange={e => setId(e.target.value)} /></label><button>Open campaign</button>
    </form>
    <h2>Create draft</h2><p>Paste a CampaignDraft manifest. The server validates all manifest fields.</p>
    <form onSubmit={e => { e.preventDefault(); if (write.isPending) return; try {
      const body = { name: name.trim(), draft: parseObject<Schema["CampaignDraft"]>(draft) };
      setError(undefined); write.mutate({ action: "create", body }, { onSuccess: data => navigate(`/admin/campaigns/${data.id}`) });
    } catch (err) { setError(err); } }}>
      <fieldset disabled={write.isPending}><legend>New campaign</legend>
        <label>Name<input required value={name} onChange={e => setName(e.target.value)} /></label>
        <label>Draft JSON<textarea required rows={12} value={draft} onChange={e => setDraft(e.target.value)} /></label>
        <button disabled={!name.trim()}>Create campaign</button>
      </fieldset>
    </form>
    {write.isPending && <Loading label="campaign creation" />}
    {Boolean(error || write.error) && <ErrorState error={error || write.error} onRetry={() => { setError(undefined); write.reset(); }} />}
  </>;
}

function CampaignDetail({ id }: { id: string }) {
  const query = useCampaign(id);
  const write = useCampaignWrite();
  const [cancelConfirmed, setCancelConfirmed] = useState(false);
  if (query.isPending) return <Loading label="campaign" />;
  if (!query.data) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  const { campaign, reservation, notice } = query.data;
  const busy = write.isPending || query.isFetching || query.isError;
  const actions = campaign.state === "frozen" ? ["start"] as const : campaign.state === "running" ? ["pause"] as const : campaign.state === "paused" ? ["resume"] as const : [];
  const canCancel = ["frozen", "running", "paused"].includes(campaign.state);
  return <>
    <p><Link to="/admin/campaigns">Open another campaign</Link> · <Link to={`/admin/campaigns/${id}/progress`}>Campaign progress</Link> · <Link to={`/admin/campaigns/${id}/publications`}>Publication review</Link></p>
    <h2>{campaign.name}</h2>
    <dl><dt>ID</dt><dd>{campaign.id}</dd><dt>Server state</dt><dd aria-live="polite">{campaign.state}</dd>
      <dt>Revision</dt><dd>{campaign.revision}</dd><dt>Manifest digest</dt><dd>{campaign.manifest_digest ?? "Not frozen"}</dd>
      <dt>Cohort digest</dt><dd>{campaign.cohort_digest ?? "Not frozen"}</dd></dl>
    <button onClick={() => void query.refetch()} disabled={query.isFetching}>Refresh campaign</button>
    {query.isError && <ErrorState error={query.error} onRetry={() => void query.refetch()} />}
    {notice && <p role="status">{notice}</p>}
    <h3>Budget reservation</h3>
    {reservation ? <dl><dt>Reservation ID</dt><dd>{reservation.reservation_id}</dd><dt>Status</dt><dd>{reservation.status}</dd>
      <dt>Reserved USD</dt><dd>{reservation.reserved_usd ?? "Unknown"}</dd><dt>Enforcement</dt><dd>{reservation.enforcement === "estimated_time_limited" ? "Estimated / time-limited — not a hard provider cap or hold" : reservation.enforcement}</dd></dl> : <p>No reservation recorded.</p>}
    {campaign.state === "draft" ? <DraftEditor key={`${id}:${campaign.revision}`} id={id} revision={campaign.revision} disabled={busy} /> : <p>The frozen plan is immutable. To change it, create a new campaign.</p>}
    {actions.map(action => <button key={action} disabled={busy} onClick={() => write.mutate({ action, id })}>{action === "start" ? "Start campaign" : action === "pause" ? "Pause campaign" : "Resume campaign"}</button>)}
    {campaign.state === "running" && <p>Pausing stops new dispatch; already leased work can finish.</p>}
    {canCancel && <fieldset disabled={busy}><legend>Cancel campaign</legend>
      <p id="cancel-warning">Cancellation stops new dispatch and releases the reservation. Leased work finishes or expires; cancellation is not immediate. Attempts remain recorded and incomplete coverage cannot silently disappear from publication.</p>
      <label><input type="checkbox" checked={cancelConfirmed} onChange={e => setCancelConfirmed(e.target.checked)} aria-describedby="cancel-warning" />I understand the cancellation consequences</label>
      <button disabled={!cancelConfirmed} onClick={() => write.mutate({ action: "cancel", id }, { onSuccess: () => setCancelConfirmed(false) })}>Confirm cancellation</button>
    </fieldset>}
    {write.isPending && <Loading label="server response" />}
    {write.error && <ErrorState error={write.error} onRetry={write.reset} />}
  </>;
}

function DraftEditor({ id, revision, disabled }: { id: string; revision: number; disabled: boolean }) {
  const [draft, setDraft] = useState("");
  const [registry, setRegistry] = useState("");
  const [error, setError] = useState<unknown>();
  const [freezeConfirmed, setFreezeConfirmed] = useState(false);
  const write = useCampaignWrite();
  const preview = useMatrixPreview(id);
  const busy = disabled || write.isPending || preview.isPending;
  function submit(action: "patch" | "preview" | "freeze") {
    if (busy) return;
    try {
      setError(undefined);
      if (action === "patch") {
        preview.reset(); setFreezeConfirmed(false);
        write.mutate({ action, id, revision, body: { draft: parseObject<Schema["CampaignDraft"]>(draft) } });
      } else {
        const body = parseObject<Schema["FreezeRegistry"]>(registry);
        if (action === "preview") preview.mutate(body);
        else if (freezeConfirmed && preview.data) write.mutate({ action, id, body });
      }
    } catch (err) { setError(err); }
  }
  return <section><h3>Edit draft</h3>
    <p>The API does not return the saved draft. Paste the complete replacement manifest; this is not a prefilled copy. Saving uses revision {revision} via If-Match. A conflict requires reloading and reconciling your source manifest.</p>
    <fieldset disabled={busy}><legend>Replace saved draft</legend>
      <label>Replacement draft JSON<textarea rows={10} value={draft} onChange={e => { setDraft(e.target.value); preview.reset(); setFreezeConfirmed(false); }} /></label>
      <button disabled={!draft.trim()} onClick={() => submit("patch")}>Save draft</button>
    </fieldset>
    <fieldset disabled={busy}><legend>Preview and freeze saved draft</legend>
      <p>Preview uses the saved server draft, not unsaved text above. Supply a FreezeRegistry with cohort, protocol, and budget.</p>
      <label>Registry JSON<textarea rows={10} value={registry} onChange={e => { setRegistry(e.target.value); preview.reset(); setFreezeConfirmed(false); }} /></label>
      <button disabled={!registry.trim()} onClick={() => submit("preview")}>Preview exact matrix</button>
      {preview.data && <><p>Server preview: {preview.data.trial_count} trials · cohort {preview.data.cohort_id} · protocol {preview.data.protocol_id} · budget {preview.data.budget_profile_id}</p>
        <p>Estimated reservation USD: {preview.data.reserved_budget_usd ?? "Unknown"} — not a provider hold.</p>
        <table><caption>Exact saved-draft matrix</caption><thead><tr><th>Task</th><th>Entrant</th><th>Repetitions</th></tr></thead>
          <tbody>{preview.data.cells.map(cell => <tr key={`${cell.task_id}:${cell.entrant_id}`}><td>{cell.task_id}</td><td>{cell.entrant_id}</td><td>{cell.repetitions}</td></tr>)}</tbody></table>
        <label><input type="checkbox" checked={freezeConfirmed} onChange={e => setFreezeConfirmed(e.target.checked)} />I understand freezing is immutable</label>
        <button disabled={!freezeConfirmed} onClick={() => submit("freeze")}>Freeze campaign</button></>}
    </fieldset>
    {(write.isPending || preview.isPending) && <Loading label="draft operation" />}
    {Boolean(error || write.error || preview.error) && <ErrorState error={error || write.error || preview.error} onRetry={() => { setError(undefined); write.reset(); preview.reset(); }} />}
  </section>;
}
