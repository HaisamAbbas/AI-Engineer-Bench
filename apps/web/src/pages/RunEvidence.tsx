import { useParams } from "react-router-dom";
import { useTrial } from "../api/hooks";
import { Loading, ErrorState, EmptyState } from "../components/QueryStates";
import { ApiRequestError } from "../api/client";

/** "/runs/:id" - verdict, requirements, diff, observable trace, usage.
 *
 * GET /v1/trials/{id} is role-gated (operator/reviewer/administrator) - spec
 * section 33 names it "Authorized | Redacted public or scoped private
 * record", but only the authorized half is implemented; a public redacted
 * view needs real redaction-boundary design (what's public-safe to show
 * from a trial before independent review/publication) that does not exist
 * yet, so it is not invented here. Anonymous visitors see an honest
 * "requires authorization" state, not a fabricated redacted view.
 *
 * The authorized response itself is also currently thin (id/phase/
 * terminal_status only) - the richer evidence tabs (diff, checks, usage)
 * the spec names are not yet returned by this endpoint, a separate,
 * disclosed gap from the auth one. Everything rendered here is plain text
 * (JSX text nodes, never dangerouslySetInnerHTML), so candidate content -
 * including any embedded HTML or terminal escape sequences - is always
 * shown as inert text, never executed. */
export function RunEvidence() {
  const { trialId } = useParams();
  const trial = useTrial(trialId);

  if (trial.isPending) return <Loading label="run evidence" />;

  if (trial.isError) {
    if (trial.error instanceof ApiRequestError && (trial.error.status === 401 || trial.error.status === 403)) {
      return (
        <section>
          <h1>Run evidence</h1>
          <EmptyState title="This run's evidence requires operator, reviewer, or administrator authorization.">
            <p>
              A public redacted view of run evidence is not implemented yet. Once a run is part of a published
              cohort, its per-task/per-entrant aggregate outcome is visible on the Results page.
            </p>
          </EmptyState>
        </section>
      );
    }
    if (trial.error instanceof ApiRequestError && trial.error.status === 404) {
      return (
        <section>
          <h1>Run evidence</h1>
          <EmptyState title="No run with this ID exists." />
        </section>
      );
    }
    return <ErrorState error={trial.error} onRetry={() => trial.refetch()} />;
  }

  const data = trial.data as {
    id: string;
    task_revision_id: string;
    entrant_revision_id: string;
    repetition: number;
    latest_attempt: { number: number; phase: string; terminal_status: string | null } | null;
  };

  return (
    <section>
      <h1>Run {data.id}</h1>
      <dl>
        <dt>Task revision</dt>
        <dd>{data.task_revision_id}</dd>
        <dt>Entrant revision</dt>
        <dd>{data.entrant_revision_id}</dd>
        <dt>Repetition</dt>
        <dd>{data.repetition}</dd>
      </dl>
      {data.latest_attempt ? (
        <dl>
          <dt>Attempt</dt>
          <dd>{data.latest_attempt.number}</dd>
          <dt>Phase</dt>
          <dd>{data.latest_attempt.phase}</dd>
          <dt>Terminal status</dt>
          <dd>{data.latest_attempt.terminal_status ?? "Not yet terminal"}</dd>
        </dl>
      ) : (
        <p>No attempt has been recorded for this run yet.</p>
      )}
      <p>
        Full evidence tabs (Outcome, Changes, Actions, Application checks, Usage, Configuration) are not yet returned
        by this endpoint - a disclosed gap, not a hidden one.
      </p>
    </section>
  );
}
