import { useParams } from "react-router-dom";
import { useTaskRevision } from "../api/hooks";
import { Loading, ErrorState } from "../components/QueryStates";

/** "/tasks/:id/:version" - ticket, environment, public requirements, origin,
 * run command. There is no separate "download public bundle" artifact
 * endpoint yet - everything shown here IS the public bundle (the manifest
 * this route returns), so "download" offers the same JSON, not a stub. */
export function TaskDetail() {
  const { slug, version } = useParams();
  const task = useTaskRevision(slug, version);

  if (task.isPending) return <Loading label="task" />;
  if (task.isError) return <ErrorState error={task.error} onRetry={() => task.refetch()} />;

  const manifest = task.data.manifest;
  const ticketText = task.data.ticket_text;

  function downloadBundle() {
    const blob = new Blob([JSON.stringify(manifest, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${manifest.id}-${manifest.version}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <section>
      <h1>
        {manifest.id} <small>v{manifest.version}</small>
      </h1>
      <dl>
        <dt>Category</dt>
        <dd>{manifest.category}</dd>
        <dt>Activity</dt>
        <dd>{manifest.activity}</dd>
        <dt>Project family</dt>
        <dd>{manifest.family_id}</dd>
        <dt>License</dt>
        <dd>{manifest.source.license}</dd>
        <dt>Dependency mode</dt>
        <dd>{manifest.application.dependency_mode}</dd>
        <dt>Egress policy</dt>
        <dd>{manifest.environment.egress_policy}</dd>
        <dt>Compute profile</dt>
        <dd>
          {manifest.environment.engineer_cpu} CPU / {manifest.environment.engineer_memory_mb} MB
        </dd>
        <dt>Profile compatibility</dt>
        <dd>{manifest.profile_compatibility.join(", ")}</dd>
      </dl>

      <h2>Task ticket</h2>
      {ticketText ? (
        <TaskTicket text={ticketText} />
      ) : (
        <p role="note">No public ticket narrative is recorded for this task revision.</p>
      )}

      <h2>Public requirements</h2>
      <table tabIndex={0}>
        <caption>Requirements this task's public contract commits to</caption>
        <thead>
          <tr>
            <th scope="col">ID</th>
            <th scope="col">Severity</th>
            <th scope="col">Description</th>
          </tr>
        </thead>
        <tbody>
          {manifest.requirements.map((requirement) => (
            <tr key={requirement.id}>
              <th scope="row">{requirement.id}</th>
              <td>{requirement.severity}</td>
              <td>{requirement.description}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2>Run command</h2>
      <pre tabIndex={0}>
        <code>{manifest.application.entrypoint.join(" ")}</code>
      </pre>

      <button type="button" onClick={downloadBundle}>
        Download public bundle
      </button>
    </section>
  );
}

function TaskTicket({ text }: { text: string }) {
  return (
    <div className="task-ticket">
      {text.trim().split(/\n\s*\n/).map((block, index) => {
        const heading = block.match(/^#{1,3}\s+(.+)$/);
        if (heading) return <h3 key={index}><InlineTicketText text={heading[1]} /></h3>;
        const lines = block.split("\n");
        if (lines.every((line) => /^[-*]\s+/.test(line))) {
          return <ul key={index}>{lines.map((line, lineIndex) => <li key={lineIndex}><InlineTicketText text={line.replace(/^[-*]\s+/, "")} /></li>)}</ul>;
        }
        return <p key={index}><InlineTicketText text={block.replace(/\n/g, " ")} /></p>;
      })}
    </div>
  );
}

function InlineTicketText({ text }: { text: string }) {
  return <>{text.split(/(`[^`]+`)/g).map((part, index) => part.startsWith("`") && part.endsWith("`")
    ? <code key={index}>{part.slice(1, -1)}</code>
    : part)}</>;
}
