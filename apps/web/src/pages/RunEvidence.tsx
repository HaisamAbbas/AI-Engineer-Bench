/** Published runs use a redacted whitelist; private evidence requires an
 * operator, reviewer, or administrator role. */
import { useState, type ReactNode } from "react";
import { useParams } from "react-router-dom";
import { useTrial, type PrivateRun, type PublicRun } from "../api/hooks";
import { ApiRequestError, downloadAuthorizedArtifact } from "../api/client";
import { Loading, ErrorState, EmptyState } from "../components/QueryStates";

type EvidenceTab = "outcome" | "changes" | "actions" | "checks" | "usage" | "configuration";
const tabs: [EvidenceTab, string][] = [
  ["outcome", "Outcome"], ["changes", "Changes"], ["actions", "Actions"],
  ["checks", "Application checks"], ["usage", "Usage"], ["configuration", "Configuration"],
];

export function RunEvidence() {
  const { trialId } = useParams();
  const [active, setActive] = useState<EvidenceTab>("outcome");
  const trial = useTrial(trialId);
  if (trial.isPending) return <Loading label="run evidence" />;
  if (trial.isError) {
    if (trial.error instanceof ApiRequestError && trial.error.status === 404) {
      return <section><h1>Run evidence</h1><EmptyState title="No public or authorized run with this ID exists." /></section>;
    }
    if (trial.error instanceof ApiRequestError && (trial.error.status === 401 || trial.error.status === 403)) {
      return <section><h1>Run evidence</h1><EmptyState title="This run requires authorization and has not been published.">
        <p>Published runs have a public redacted evidence view. Private run details require an authorized role.</p>
      </EmptyState></section>;
    }
    return <ErrorState error={trial.error} onRetry={() => trial.refetch()} />;
  }
  return trial.data.visibility === "public"
    ? <PublicEvidence data={trial.data.data} active={active} setActive={setActive} />
    : <PrivateEvidence data={trial.data.data} active={active} setActive={setActive} />;
}

function PublicEvidence({ data, active, setActive }: { data: PublicRun; active: EvidenceTab; setActive: (tab: EvidenceTab) => void }) {
  return <section>
    <h1>Run {data.trial_id.slice(0, 8)}</h1>
    <p role="note">Public redacted evidence from publication {data.publication_id}. It is bound to evaluation {data.evaluation_id ?? "not scored"}. Candidate source, engineering output, evaluator diagnostics, and private artifact references are withheld.</p>
    <EvidenceTabs active={active} onChange={setActive} />
    {active === "outcome" && <Panel id="outcome"><dl>
      <dt>Trial ID</dt><dd>{data.trial_id}</dd>
      <dt>Task</dt><dd>{data.task_id} v{data.task_version}</dd>
      <dt>Entrant</dt><dd>{data.entrant_id} v{data.entrant_version}</dd>
      <dt>Repetition</dt><dd>{data.repetition + 1}</dd>
      <dt>Attempt status</dt><dd>{data.attempt?.terminal_status ?? data.attempt?.phase ?? "No attempt selected"}</dd>
      <dt>Evaluation state</dt><dd>{data.evaluation_state}</dd>
      <dt>Verdict</dt><dd>{data.verdict ?? "Not scored"}</dd>
    </dl>{data.evaluation_state === "invalid" && <p role="status">This attempt is invalid and has no scientific verdict.</p>}</Panel>}
    {active === "changes" && <Panel id="changes"><h2>Candidate changes</h2><p>Candidate source and file changes are withheld from public evidence.</p></Panel>}
    {active === "actions" && <Panel id="actions"><h2>Observable trace and actions</h2><Trace state={data.trace_state} events={data.trace} /><p>Private artifacts are not available in the public view.</p></Panel>}
    {active === "checks" && <Panel id="checks"><h2>Application checks</h2>
      {data.checks.length ? <ul>{data.checks.map((check) => <li key={check.requirement_id}>{check.requirement_id}: {check.passed === null ? "Unknown" : check.passed ? "Pass" : "Fail"}</li>)}</ul> : <p>No requirement checks are recorded for this run.</p>}
    </Panel>}
    {active === "usage" && <Panel id="usage"><h2>Usage</h2><Usage usage={data.usage} publicView /></Panel>}
    {active === "configuration" && <Panel id="configuration"><h2>Configuration</h2><Configuration values={data.configuration} /></Panel>}
  </section>;
}

function PrivateEvidence({ data, active, setActive }: { data: PrivateRun; active: EvidenceTab; setActive: (tab: EvidenceTab) => void }) {
  return <section>
    <h1>Run {data.id.slice(0, 8)}</h1>
    <EvidenceTabs active={active} onChange={setActive} />
    {active === "outcome" && <Panel id="outcome"><dl>
      <dt>Trial ID</dt><dd>{data.id}</dd>
      <dt>Task revision</dt><dd>{data.task_revision_id}</dd>
      <dt>Entrant revision</dt><dd>{data.entrant_revision_id}</dd>
      <dt>Repetition</dt><dd>{data.repetition + 1}</dd>
      {data.latest_attempt && <><dt>Attempt</dt><dd>{data.latest_attempt.number}</dd><dt>Phase</dt><dd>{data.latest_attempt.phase}</dd><dt>Terminal status</dt><dd>{data.latest_attempt.terminal_status ?? "Not yet terminal"}</dd></>}
      <dt>Evaluation state</dt><dd>{data.evaluation_state}</dd>
      {data.evaluation_id && <><dt>Evaluation ID</dt><dd>{data.evaluation_id}</dd></>}
      <dt>Verdict</dt><dd>{data.verdict ?? "Not scored"}</dd>
      {data.superseded_evaluation_count > 0 && <><dt>Earlier evaluations</dt><dd>{data.superseded_evaluation_count} superseded by the selected attempt</dd></>}
    </dl>
    {data.evaluation_state === "invalid" && <p role="status">This attempt is invalid and has no scientific verdict.</p>}
    {data.evaluation_state === "superseded" && <p role="status">A newer attempt superseded the available evaluation.</p>}</Panel>}
    {active === "changes" && <Panel id="changes">
      <h2>Candidate output</h2>
      {data.engineering_stdout || data.engineering_stderr ? <>
        {data.engineering_logs_truncated && <p role="note">Output is truncated to the first 64 KiB of each stream.</p>}
        {data.engineering_stdout && <><h3>Standard output</h3><pre tabIndex={0} className="run-log"><code>{data.engineering_stdout}</code></pre></>}
        {data.engineering_stderr && <><h3>Standard error</h3><pre tabIndex={0} className="run-log"><code>{data.engineering_stderr}</code></pre></>}
      </> : <p>No candidate output was captured.</p>}
      <h2>Candidate diff</h2>
      {data.diffs.length ? data.diffs.map((diff) => <article key={diff.path}>
        <h3>{diff.path} ({diff.operation})</h3>
        {diff.unified_diff !== null ? <pre tabIndex={0} className="run-diff"><code>{diff.unified_diff}</code></pre> : <p>{diff.binary ? "Binary file; text diff is unavailable." : diff.truncated ? "Diff exceeded the display limit." : "Diff is unavailable."}</p>}
        {!diff.baseline_available && <p role="note">The frozen source baseline was unavailable when evidence was collected.</p>}
      </article>) : <p>No candidate file changes are recorded.</p>}
    </Panel>}
    {active === "actions" && <Panel id="actions"><h2>Observable trace and actions</h2>
      <Trace state={data.trace_state} events={data.trace} />
      {data.artifacts.length > 0 && <><h2>Authorized artifacts</h2><ul>{data.artifacts.map((artifact) => <li key={artifact.artifact_ref_id}>
        <button type="button" onClick={() => void downloadAuthorizedArtifact(data.id, artifact.artifact_ref_id, artifact.path)}>Download {artifact.path} ({artifact.size} bytes)</button>
      </li>)}</ul></>}
    </Panel>}
    {active === "checks" && <Panel id="checks"><h2>Application checks</h2>
      {data.checks && Object.keys(data.checks).length ? <ul>{Object.entries(data.checks).map(([key, passed]) => <li key={key}>{key}: {passed ? "Pass" : "Fail"}</li>)}</ul> : <p>No requirement checks are recorded.</p>}
      {data.diagnostics && Object.keys(data.diagnostics).length > 0 && <><h2>Evaluator diagnostics</h2><dl>{Object.entries(data.diagnostics).map(([key, value]) => <div key={key}><dt>{key}</dt><dd><pre tabIndex={0} className="run-log"><code>{value}</code></pre></dd></div>)}</dl></>}
    </Panel>}
    {active === "usage" && <Panel id="usage"><h2>Usage</h2><Usage usage={data.usage} /></Panel>}
    {active === "configuration" && <Panel id="configuration"><h2>Configuration</h2><Configuration values={data.configuration} /></Panel>}
  </section>;
}

function EvidenceTabs({ active, onChange }: { active: EvidenceTab; onChange: (tab: EvidenceTab) => void }) {
  return <div role="tablist" aria-label="Run evidence sections" className="evidence-tabs">
    {tabs.map(([id, label]) => <button key={id} id={`tab-${id}`} role="tab" aria-selected={active === id} tabIndex={active === id ? 0 : -1} onClick={() => onChange(id)}>{label}</button>)}
  </div>;
}

function Panel({ id, children }: { id: EvidenceTab; children: ReactNode }) {
  return <section id={`panel-${id}`} role="tabpanel" aria-labelledby={`tab-${id}`} tabIndex={0}>{children}</section>;
}

function Trace({ state, events }: { state: "complete" | "partial" | "unavailable"; events: { sequence: number; event_type: string; payload: Record<string, string | number | boolean | null> }[] }) {
  if (state === "unavailable" || events.length === 0) return <p>Observable trace was not recorded.</p>;
  return <>{state === "partial" && <p role="status">This trace is partial; the recorded actions do not cover the full run.</p>}
    <ol className="run-trace">{events.map((event) => <li key={`${event.sequence}-${event.event_type}`}><strong>{event.event_type}</strong> <span>{JSON.stringify(event.payload)}</span></li>)}</ol>
  </>;
}

function Usage({ usage, publicView = false }: { usage: { input_tokens?: number | null; output_tokens?: number | null; cost_usd?: string | null } | null | undefined; publicView?: boolean }) {
  if (!usage) return <p>Usage was not recorded for this evaluation.</p>;
  return <><dl><dt>Input tokens</dt><dd>{usage.input_tokens ?? "Not recorded"}</dd><dt>Output tokens</dt><dd>{usage.output_tokens ?? "Not recorded"}</dd>
    {!publicView && <><dt>Reported cost</dt><dd>{usage.cost_usd ?? "Not recorded"}</dd></>}
  </dl>{publicView && <p>Token counts are public when present. Cost details are reserved for authorized views.</p>}</>;
}

function Configuration({ values }: { values: Record<string, string | string[] | null> }) {
  return Object.keys(values).length ? <dl>{Object.entries(values).map(([key, value]) => <div key={key}><dt>{key.replaceAll("_", " ")}</dt><dd>{Array.isArray(value) ? value.join(", ") : value ?? "Not recorded"}</dd></div>)}</dl> : <p>Configuration details were not recorded.</p>;
}
