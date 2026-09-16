/** Published runs use a public whitelist projection. Unpublished runs are
 * available only to operator/reviewer/administrator roles and include the
 * collected candidate diff and bounded engineering output. */
import { useParams } from "react-router-dom";
import { useTrial } from "../api/hooks";
import { Loading, ErrorState, EmptyState } from "../components/QueryStates";
import { ApiRequestError } from "../api/client";

export function RunEvidence() {
  const { trialId } = useParams();
  const trial = useTrial(trialId);

  if (trial.isPending) return <Loading label="run evidence" />;
  if (trial.isError) {
    if (trial.error instanceof ApiRequestError && trial.error.status === 404) {
      return <section><h1>Run evidence</h1><EmptyState title="No public or authorized run with this ID exists." /></section>;
    }
    if (trial.error instanceof ApiRequestError && (trial.error.status === 401 || trial.error.status === 403)) {
      return (
        <section>
          <h1>Run evidence</h1>
          <EmptyState title="This run requires authorization and has not been published.">
            <p>Published runs have a public redacted evidence view. Private run details require an authorized role.</p>
          </EmptyState>
        </section>
      );
    }
    return <ErrorState error={trial.error} onRetry={() => trial.refetch()} />;
  }

  if (trial.data.visibility === "public") {
    const data = trial.data.data;
    return (
      <section>
        <h1>Run {data.trial_id.slice(0, 8)}</h1>
        <p role="note">
          Public redacted evidence from publication {data.publication_id}. Candidate source, engineering output,
          evaluator diagnostics, fixture details, and private artifact references are withheld.
        </p>
        <dl>
          <dt>Trial ID</dt><dd>{data.trial_id}</dd>
          <dt>Task</dt><dd>{data.task_id} v{data.task_version}</dd>
          <dt>Entrant</dt><dd>{data.entrant_id} v{data.entrant_version}</dd>
          <dt>Repetition</dt><dd>{data.repetition + 1}</dd>
          <dt>Verdict</dt><dd>{data.verdict ?? "Not scored"}</dd>
          {data.attempt && <><dt>Attempt status</dt><dd>{data.attempt.terminal_status ?? data.attempt.phase}</dd></>}
        </dl>
        <h2>Requirement outcomes</h2>
        {data.checks.length === 0 ? <p>No requirement checks are recorded for this run.</p> : (
          <ul>
            {data.checks.map((check) => <li key={check.requirement_id}>{check.requirement_id}: {check.passed === null ? "Unknown" : check.passed ? "Pass" : "Fail"}</li>)}
          </ul>
        )}
      </section>
    );
  }

  const data = trial.data.data;
  return (
    <section>
      <h1>Run {data.id.slice(0, 8)}</h1>
      <dl>
        <dt>Trial ID</dt><dd>{data.id}</dd>
        <dt>Task revision</dt><dd>{data.task_revision_id}</dd>
        <dt>Entrant revision</dt><dd>{data.entrant_revision_id}</dd>
        <dt>Repetition</dt><dd>{data.repetition + 1}</dd>
        {data.latest_attempt && <>
          <dt>Attempt</dt><dd>{data.latest_attempt.number}</dd>
          <dt>Phase</dt><dd>{data.latest_attempt.phase}</dd>
          <dt>Terminal status</dt><dd>{data.latest_attempt.terminal_status ?? "Not yet terminal"}</dd>
        </>}
        {data.verdict && <><dt>Verdict</dt><dd>{data.verdict}</dd></>}
      </dl>

      <h2>Requirement checks</h2>
      {data.checks && Object.keys(data.checks).length > 0 ? (
        <ul>{Object.entries(data.checks).map(([key, passed]) => <li key={key}>{key}: {passed ? "Pass" : "Fail"}</li>)}</ul>
      ) : <p>No requirement checks are recorded.</p>}

      <h2>Candidate output</h2>
      {data.engineering_stdout || data.engineering_stderr ? (
        <>
          {data.engineering_logs_truncated && <p role="note">Output is truncated to the first 64 KiB of each stream.</p>}
          {data.engineering_stdout && <><h3>Standard output</h3><pre tabIndex={0} className="run-log"><code>{data.engineering_stdout}</code></pre></>}
          {data.engineering_stderr && <><h3>Standard error</h3><pre tabIndex={0} className="run-log"><code>{data.engineering_stderr}</code></pre></>}
        </>
      ) : <p>No candidate output was captured.</p>}

      <h2>Candidate diff</h2>
      {data.diffs.length > 0 ? data.diffs.map((diff) => (
        <article key={diff.path}>
          <h3>{diff.path} ({diff.operation})</h3>
          {diff.unified_diff !== null ? <pre tabIndex={0} className="run-diff"><code>{diff.unified_diff}</code></pre> : (
            <p>{diff.binary ? "Binary file; text diff is unavailable." : diff.truncated ? "Diff exceeded the display limit." : "Diff is unavailable."}</p>
          )}
          {!diff.baseline_available && <p role="note">The frozen source baseline was unavailable when evidence was collected.</p>}
        </article>
      )) : <p>No candidate file changes are recorded.</p>}

      {data.diagnostics && Object.keys(data.diagnostics).length > 0 && (
        <>
          <h2>Evaluator diagnostics</h2>
          <dl>{Object.entries(data.diagnostics).map(([key, value]) => <div key={key}><dt>{key}</dt><dd><pre tabIndex={0} className="run-log"><code>{value}</code></pre></dd></div>)}</dl>
        </>
      )}
    </section>
  );
}
