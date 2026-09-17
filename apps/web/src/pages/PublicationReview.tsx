import { useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import {
  usePreparePublication, useReviewPublication, useWithdrawPublication,
  useRegradePublication, usePublicationScoringBundle, usePublicationCorrectionRun, usePublicationExport,
  type PublicationPreparation, type PublicationReviewInput, type PublicationRegradeInput,
  type PublicationExportData,
} from "../api/publicationHooks";
import { ErrorState, Loading } from "../components/QueryStates";

export function PublicationReview() {
  const { campaignId } = useParams<{ campaignId: string }>();
  if (!campaignId) return <section><h1>Publication review</h1><p>A campaign ID is required.</p></section>;
  // Reset local confirmations and mutation results when changing campaigns.
  return <PublicationWorkspace key={campaignId} campaignId={campaignId} />;
}

function PublicationWorkspace({ campaignId }: { campaignId: string }) {
  return <section className="admin-page">
    <h1>Publication review</h1>
    <p>Campaign: <code>{campaignId}</code> · <Link to={`/admin/campaigns/${campaignId}/progress`}>Campaign progress</Link></p>
    <p><strong>Staging / fixture use only. Real public publication is not authorized.</strong></p>
    <p>The server enforces roles and campaign eligibility. Preparing requires an operator; reviewing, withdrawing,
      and regrading require a reviewer or administrator.</p>
    <PreparationReview campaignId={campaignId} />
    <Regrade campaignId={campaignId} />
    <WithdrawAndExport campaignId={campaignId} />
  </section>;
}

function MutationFeedback({ pending, error, reset, label }: {
  pending: boolean; error: unknown; reset: () => void; label: string;
}) {
  return <>
    {pending && <Loading label={label} />}
    {Boolean(error) && <><ErrorState error={error} onRetry={reset} />
      <p>Retry clears this error only. Check server state before explicitly submitting again; writes are not automatically retried.</p></>}
  </>;
}

function PreparationSummary({ data }: { data: PublicationPreparation }) {
  return <div aria-live="polite">
    <h3>Last preparation response</h3>
    <dl>
      <dt>Preparation ID</dt><dd><code>{data.id}</code></dd>
      <dt>Campaign</dt><dd>{data.campaign_id}</dd>
      <dt>State</dt><dd>{data.status}</dd>
      <dt>Snapshot digest</dt><dd><code>{data.snapshot_digest}</code></dd>
      <dt>Evidence manifest digest</dt><dd><code>{data.evidence_manifest_digest}</code></dd>
      <dt>Review kind</dt><dd>{data.review_kind ?? "Not reviewed"}</dd>
      <dt>Supersedes publication</dt><dd>{data.supersedes_publication_id ?? "None"}</dd>
      <dt>Published publication ID</dt><dd>{data.published_publication_id ?
        <Link to={`/releases/${data.published_publication_id}`}>{data.published_publication_id}</Link> : "Not published"}</dd>
      <dt>Created at</dt><dd>{data.created_at}</dd>
    </dl>
    <p>This is a write response, not a live preparation lookup.</p>
  </div>;
}

function PreparationReview({ campaignId }: { campaignId: string }) {
  const prepare = usePreparePublication(campaignId);
  const review = useReviewPublication();
  const [supersedes, setSupersedes] = useState("");
  const [correctionRun, setCorrectionRun] = useState("");
  const [reason, setReason] = useState("");
  const [preparationId, setPreparationId] = useState("");
  const [decision, setDecision] = useState<PublicationReviewInput["decision"]>("reject");
  const [kind, setKind] = useState<PublicationReviewInput["review_kind"]>("single_maintainer");
  const [notes, setNotes] = useState("");
  const [fixture, setFixture] = useState(false);
  const [localIds, setLocalIds] = useState<Set<string>>(() => new Set());
  const [responses, setResponses] = useState<Record<string, PublicationPreparation>>({});
  const [last, setLast] = useState<PublicationPreparation>();
  const normalizedId = preparationId.trim().toLowerCase();
  const ownPreparation = localIds.has(normalizedId);
  const known = responses[normalizedId];
  const busy = prepare.isPending || review.isPending;
  const reviewBlocked = busy || !normalizedId || (known !== undefined && known.status !== "prepared") ||
    (decision === "approve" && (!fixture || ownPreparation));

  function remember(data: PublicationPreparation) {
    setLast(data);
    setResponses((previous) => ({ ...previous, [data.id.toLowerCase()]: data }));
  }
  function submitPrepare(event: FormEvent) {
    event.preventDefault();
    if (busy || (supersedes.trim() && (!correctionRun.trim() || !reason.trim()))) return;
    prepare.mutate({
      supersedes_publication_id: supersedes.trim() || null,
      correction_run_id: correctionRun.trim() || null,
      correction_reason: reason.trim() || null,
    }, { onSuccess: (data) => {
      remember(data);
      setLocalIds((previous) => new Set([...previous, data.id.toLowerCase()]));
      setPreparationId(data.id);
      setFixture(false);
    } });
  }
  function submitReview(event: FormEvent) {
    event.preventDefault();
    if (reviewBlocked) return;
    review.mutate({ preparationId: normalizedId, body: { decision, review_kind: kind, notes: notes.trim() || null } },
      { onSuccess: (data) => { remember(data); setFixture(false); } });
  }

  return <section aria-labelledby="prepare-heading">
    <h2 id="prepare-heading">Prepare and review</h2>
    <p>Only completed or incomplete campaigns can be prepared. Preparation does not publish.</p>
    <form onSubmit={submitPrepare} aria-label="Prepare publication">
      <fieldset disabled={busy}>
        <legend>Prepare a snapshot</legend>
        <label>Supersedes publication ID (optional)<input value={supersedes} onChange={(e) => setSupersedes(e.target.value)} /></label>
        <label>Completed correction run ID (optional)<input value={correctionRun} onChange={(e) => setCorrectionRun(e.target.value)} required={Boolean(supersedes.trim())} /></label>
        <label>Correction reason<textarea value={reason} onChange={(e) => setReason(e.target.value)} required={Boolean(supersedes.trim())} /></label>
        <p>Superseding a publication requires its completed correction run and a reason; the server validates campaign membership.</p>
        <button type="submit" disabled={Boolean(supersedes.trim()) && (!correctionRun.trim() || !reason.trim())}>Prepare publication</button>
      </fieldset>
    </form>
    <MutationFeedback pending={prepare.isPending} error={prepare.error} reset={prepare.reset} label="preparation" />
    {last && <PreparationSummary data={last} />}
    <p>No preparation GET or list endpoint is available. Enter an ID supplied by the preparing operator and review
      its digests through your out-of-band review process. The server checks its current state.</p>
    <p>Identity IDs are not exposed by this API, so this page cannot verify who prepared a manually entered ID
      or created the campaign. Approval of IDs prepared in this page session is blocked; the server enforces self-approval rules.</p>
    <form onSubmit={submitReview} aria-label="Review preparation">
      <fieldset disabled={busy}>
        <legend>Review an existing preparation</legend>
        <label>Preparation ID<input required value={preparationId} onChange={(e) => { setPreparationId(e.target.value); setFixture(false); }} /></label>
        <label>Decision<select value={decision} onChange={(e) => { setDecision(e.target.value as PublicationReviewInput["decision"]); setFixture(false); }}>
          <option value="reject">Reject — does not publish</option>
          <option value="approve">Approve and publish immediately</option>
        </select></label>
        <label>Review kind<select value={kind} onChange={(e) => { setKind(e.target.value as PublicationReviewInput["review_kind"]); setFixture(false); }}>
          <option value="single_maintainer">Single-maintainer review (not independent)</option>
          <option value="independent">Independent review</option>
        </select></label>
        <p>Single-maintainer is a disclosed review label, not a waiver of self-approval rules. Select independent only when an independent review occurred.</p>
        <label>Review notes (optional)<textarea value={notes} onChange={(e) => setNotes(e.target.value)} /></label>
        <label><input type="checkbox" checked={fixture} onChange={(e) => setFixture(e.target.checked)} />
          I confirm this is a staging fixture, not a real public publication.</label>
        {ownPreparation && <p role="status">Approval blocked: this ID was prepared in this page session. Ask a different authorized reviewer.</p>}
        {known && known.status !== "prepared" && <p role="status">This preparation is already {known.status}; it cannot be reviewed again.</p>}
        <button type="submit" disabled={reviewBlocked}>{decision === "approve" ? "Approve and publish staging fixture" : "Reject preparation"}</button>
      </fieldset>
    </form>
    <MutationFeedback pending={review.isPending} error={review.error} reset={review.reset} label="review decision" />
  </section>;
}

function Regrade({ campaignId }: { campaignId: string }) {
  const regrade = useRegradePublication(campaignId);
  const [loadBundle, setLoadBundle] = useState(false);
  const bundle = usePublicationScoringBundle(loadBundle ? campaignId : undefined);
  const [registryText, setRegistryText] = useState("");
  const [reason, setReason] = useState("");
  const [validation, setValidation] = useState("");
  const [runInput, setRunInput] = useState("");
  const [runId, setRunId] = useState<string>();
  const run = usePublicationCorrectionRun(runId);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (regrade.isPending || !reason.trim()) return;
    try {
      const parsed: unknown = JSON.parse(registryText);
      if (!isObject(parsed) || !isObject(parsed.cohort) || !isObject(parsed.protocol) || !isObject(parsed.budget) ||
        typeof parsed.protocol.scoring_digest !== "string" || !parsed.protocol.scoring_digest.trim()) {
        throw new Error("Registry must contain cohort, protocol (with scoring_digest), and budget objects.");
      }
      setValidation("");
      // JSON is operator-supplied. Nested manifest validation belongs to the API,
      // which also compares it to the frozen registry unavailable to this client.
      regrade.mutate({ registry: parsed as PublicationRegradeInput["registry"], reason: reason.trim() }, {
        onSuccess: (data) => { setRunInput(data.id); setRunId(data.id); },
      });
    } catch (error) {
      setValidation(error instanceof SyntaxError ? "Registry must be valid JSON." : (error as Error).message);
    }
  }

  return <section aria-labelledby="regrade-heading">
    <h2 id="regrade-heading">Regrade retained candidates</h2>
    <p>Local / staging fixtures only. Regrading queues new evaluations; it does not publish or overwrite the original evidence.</p>
    <p>The frozen registry is not exposed by the API. Obtain the exact frozen cohort, protocol, and budget JSON from the operator.
      Keep cohort and budget unchanged; only protocol.scoring_digest may change. The scoring-bundle GET returns an installed
      staging scoring digest, not a registry or an editable evaluator bundle.</p>
    <button type="button" disabled={bundle.isFetching} onClick={() => { if (loadBundle) void bundle.refetch(); else setLoadBundle(true); }}>Get scoring bundle</button>
    {loadBundle && bundle.isPending && <Loading label="scoring bundle" />}
    {bundle.isError && <ErrorState error={bundle.error} onRetry={() => bundle.refetch()} />}
    {bundle.data && <dl>
      <dt>Installed scoring digest</dt><dd><code>{String(bundle.data.scoring_digest ?? "Unavailable")}</code></dd>
      <dt>Scoring bundle scope</dt><dd>{String(bundle.data.scope ?? "Unavailable")}</dd>
    </dl>}
    <form onSubmit={submit} aria-label="Regrade campaign">
      <fieldset disabled={regrade.isPending}>
        <legend>Submit a scoring correction</legend>
        <label>Registry JSON<textarea required rows={8} value={registryText} onChange={(e) => { setRegistryText(e.target.value); setValidation(""); }} /></label>
        <label>Regrade reason<textarea required value={reason} onChange={(e) => setReason(e.target.value)} /></label>
        {validation && <p role="alert">{validation}</p>}
        <button type="submit" disabled={!registryText.trim() || !reason.trim()}>Start staging regrade</button>
      </fieldset>
    </form>
    <MutationFeedback pending={regrade.isPending} error={regrade.error} reset={regrade.reset} label="regrade request" />
    {regrade.data && <p role="status">Regrade accepted: {regrade.data.id}. Initial state: {regrade.data.status}.</p>}
    <form aria-label="Correction run status" onSubmit={(e) => { e.preventDefault(); if (runInput.trim()) { if (runInput.trim() === runId) void run.refetch(); else setRunId(runInput.trim()); } }}>
      <label>Correction run ID<input required value={runInput} onChange={(e) => setRunInput(e.target.value)} /></label>
      <button type="submit" disabled={!runInput.trim() || run.isFetching}>Check correction run</button>
    </form>
    {runId && run.isPending && <Loading label="correction run" />}
    {run.isError && <ErrorState error={run.error} onRetry={() => run.refetch()} />}
    {run.data && <div aria-live="polite">
      <dl><dt>Run ID</dt><dd>{run.data.id}</dd><dt>Run campaign</dt><dd>{run.data.campaign_id}</dd>
        <dt>Run state</dt><dd>{run.data.status}</dd><dt>Regrade work items</dt><dd>{run.data.regrade_work_items}</dd></dl>
      {run.data.campaign_id !== campaignId ? <p role="alert">This correction run belongs to a different campaign. Do not use it to prepare this campaign.</p> :
        run.data.status === "completed" ? <p>Correction completed. Copy this run ID into preparation above, with the publication to supersede and correction reason. A separate review is still required.</p> :
        run.data.status === "failed" ? <p>Correction failed. Do not prepare from this run; ask the operator to investigate.</p> :
        <p>Polling every two seconds while running. A completed run is required before corrected preparation.</p>}
    </div>}
  </section>;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function WithdrawAndExport({ campaignId }: { campaignId: string }) {
  const withdraw = useWithdrawPublication();
  const [publicationId, setPublicationId] = useState("");
  const [reason, setReason] = useState("");
  const [exportInput, setExportInput] = useState("");
  const [exportId, setExportId] = useState<string>();
  const exported = usePublicationExport(exportId);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (withdraw.isPending || !publicationId.trim() || !reason.trim()) return;
    withdraw.mutate({ publicationId: publicationId.trim(), reason: reason.trim() });
  }
  return <section aria-labelledby="withdraw-heading">
    <h2 id="withdraw-heading">Withdrawal and public export</h2>
    <p>Withdrawal changes publication status, not the retained snapshot. Enter the exact publication ID and a reason.
      This endpoint is publication-scoped, not campaign-scoped: check its export before withdrawing.</p>
    <form onSubmit={submit} aria-label="Withdraw publication">
      <fieldset disabled={withdraw.isPending}>
        <legend>Withdraw a publication</legend>
        <label>Publication ID to withdraw<input required value={publicationId} onChange={(e) => setPublicationId(e.target.value)} /></label>
        <label>Withdrawal reason<textarea required value={reason} onChange={(e) => setReason(e.target.value)} /></label>
        <button type="submit" disabled={!publicationId.trim() || !reason.trim()}>Withdraw publication</button>
      </fieldset>
    </form>
    <MutationFeedback pending={withdraw.isPending} error={withdraw.error} reset={withdraw.reset} label="withdrawal" />
    {withdraw.data && <p role="status">Withdrawal response for {withdraw.data.publication_id}: {withdraw.data.status}.</p>}
    <form aria-label="Load publication export" onSubmit={(e) => {
      e.preventDefault();
      if (!exportInput.trim()) return;
      if (exportInput.trim() === exportId) void exported.refetch(); else setExportId(exportInput.trim());
    }}>
      <label>Publication ID to export<input required value={exportInput} onChange={(e) => setExportInput(e.target.value)} /></label>
      <button type="submit" disabled={!exportInput.trim() || exported.isFetching}>Load public export</button>
    </form>
    {exportId && exported.isPending && <Loading label="publication export" />}
    {exported.isError && <ErrorState error={exported.error} onRetry={() => exported.refetch()} />}
    {exported.data && <ExportSummary data={exported.data} campaignId={campaignId} />}
  </section>;
}

function ExportSummary({ data, campaignId }: { data: PublicationExportData; campaignId: string }) {
  return <div>
    <h3>Public export</h3>
    <dl>
      <dt>Publication ID</dt><dd>{data.publication_id}</dd>
      <dt>Export campaign</dt><dd>{data.campaign_id}</dd>
      <dt>Publication state</dt><dd>{data.status}</dd>
      <dt>Export snapshot digest</dt><dd><code>{data.snapshot_digest}</code></dd>
      <dt>Frozen tasks</dt><dd>{data.frozen_tasks.length}</dd>
      <dt>Frozen entrants</dt><dd>{data.frozen_entrants.length}</dd>
      <dt>Public runs</dt><dd>{data.runs.length}</dd>
      <dt>Signing key ID</dt><dd>{data.signature?.signing_key_id ?? "No signature available"}</dd>
      <dt>Export review kind</dt><dd>{data.signature?.review_kind ?? "Unavailable"}</dd>
    </dl>
    {data.campaign_id !== campaignId && <p role="alert">This publication belongs to a different campaign.</p>}
    {data.notice && <p>{data.notice}</p>}
    <p>This redacted export contains public evidence only. Displaying a signature is not cryptographic verification.</p>
    <details><summary>View complete public export JSON</summary><pre>{JSON.stringify(data, null, 2)}</pre></details>
  </div>;
}

