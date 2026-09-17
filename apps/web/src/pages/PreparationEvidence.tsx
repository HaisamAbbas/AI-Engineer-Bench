import { useState } from "react";
import { usePublicationPreparation } from "../api/publicationHooks";
import { ErrorState, Loading } from "../components/QueryStates";

export function PreparationEvidence({ campaignId }: { campaignId: string }) {
  const [input, setInput] = useState("");
  const [id, setId] = useState<string>();
  const query = usePublicationPreparation(id);
  return <section aria-label="Preparation evidence">
    <h2>Inspect prepared materials</h2>
    <form onSubmit={e => { e.preventDefault(); const next = input.trim(); if (next) {
      if (next === id) void query.refetch(); else setId(next);
    } }}>
      <label>Preparation ID to inspect<input required value={input} onChange={e => setInput(e.target.value)} /></label>
      <button disabled={!input.trim() || query.isFetching}>Load preparation evidence</button>
    </form>
    {id && query.isPending && <Loading label="prepared evidence" />}
    {query.isError && <ErrorState error={query.error} onRetry={() => void query.refetch()} />}
    {query.data && !query.isError && (query.data.preparation.campaign_id !== campaignId
      ? <p role="alert">This preparation belongs to another campaign.</p>
      : <>
        <p>Preparation: {query.data.preparation.id} — {query.data.preparation.status}</p>
        <p>Snapshot digest: <code>{query.data.preparation.snapshot_digest}</code></p>
        <p>Evidence digest: <code>{query.data.preparation.evidence_manifest_digest}</code></p>
        <p>{query.data.can_approve ? "Server permits this identity to approve; review the materials before deciding."
          : query.data.approval_blocked_reason}</p>
        <p>Correction reason: {query.data.correction_reason ?? "None"}</p>
        <details open><summary>Proposed snapshot and coverage</summary><pre>{JSON.stringify(query.data.snapshot, null, 2)}</pre></details>
        <details><summary>Pinned evaluation selections</summary><pre>{JSON.stringify(query.data.evidence_manifest, null, 2)}</pre></details>
        <p>These materials are not a publication. The server revalidates evidence and identity when a decision is submitted.</p>
      </>)}
  </section>;
}
