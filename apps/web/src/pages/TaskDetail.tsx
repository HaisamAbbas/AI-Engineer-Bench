import { useParams } from "react-router-dom";
import { useTaskRevision } from "../api/hooks";
import { Loading, ErrorState } from "../components/QueryStates";

interface TaskManifest {
  id: string;
  version: string;
  family_id: string;
  category: string;
  activity: string;
  source: { commit: string; license: string };
  environment: { official_image: string; engineer_cpu: number; engineer_memory_mb: number; egress_policy: string };
  application: { dependency_mode: string; entrypoint: string[]; model_profile_id: string };
  submission: { include: string[]; protected: string[]; max_artifact_bytes: number };
  requirements: { id: string; severity: string; description: string }[];
  profile_compatibility: string[];
}

/** "/tasks/:id/:version" - ticket, environment, public requirements, origin,
 * run command. There is no separate "download public bundle" artifact
 * endpoint yet - everything shown here IS the public bundle (the manifest
 * this route returns), so "download" offers the same JSON, not a stub. */
export function TaskDetail() {
  const { slug, version } = useParams();
  const task = useTaskRevision(slug, version);

  if (task.isPending) return <Loading label="task" />;
  if (task.isError) return <ErrorState error={task.error} onRetry={() => task.refetch()} />;

  const manifest = task.data.manifest as TaskManifest;

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

      <h2>Public requirements</h2>
      <table>
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
      <pre>
        <code>{manifest.application.entrypoint.join(" ")}</code>
      </pre>

      <button type="button" onClick={downloadBundle}>
        Download public bundle
      </button>
    </section>
  );
}
