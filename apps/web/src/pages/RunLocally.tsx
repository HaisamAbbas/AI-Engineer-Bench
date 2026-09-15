import { useState } from "react";

interface Command {
  label: string;
  command: string;
}

const INSTALL: Command[] = [{ label: "Install workspace dependencies", command: "uv sync --all-packages --locked" }];
const RUN: Command[] = [
  { label: "Check local capability", command: "aieb doctor" },
  { label: "Validate a task", command: "aieb task validate suites/dev/rag.document-freshness" },
  { label: "Plan a local campaign", command: "aieb plan --campaign examples/rag01-local-campaign.json" },
  { label: "Run a local campaign", command: "aieb run --campaign examples/rag01-local-campaign.json" },
  { label: "Generate an HTML report", command: "aieb report --campaign <campaign-id> --format html" },
];
const TROUBLESHOOT: Command[] = [
  { label: "Run the full local test suite", command: "python -m unittest discover -s tests -p \"test_*.py\"" },
];

/** "/docs" - install/run/author/troubleshoot documentation, copy commands,
 * version picker. Commands are the same ones documented in
 * docs/implementation/SESSION_HANDOFF.md, kept here so a reader never needs
 * repository access to get started. */
export function RunLocally() {
  const [version] = useState("development (unreleased)");
  const [copied, setCopied] = useState<string | null>(null);

  function copy(command: string) {
    navigator.clipboard?.writeText(command);
    setCopied(command);
    window.setTimeout(() => setCopied(null), 2000);
  }

  function Section({ title, commands }: { title: string; commands: Command[] }) {
    return (
      <>
        <h2>{title}</h2>
        <ul>
          {commands.map((item) => (
            <li key={item.command}>
              <p>{item.label}</p>
              <pre>
                <code>{item.command}</code>
              </pre>
              <button type="button" onClick={() => copy(item.command)}>
                {copied === item.command ? "Copied" : "Copy"}
              </button>
            </li>
          ))}
        </ul>
      </>
    );
  }

  return (
    <section>
      <h1>Run locally</h1>
      <label htmlFor="version-picker">CLI version</label>
      <select id="version-picker" value={version} disabled>
        <option value={version}>{version}</option>
      </select>
      <p>Local reports require no hosted account. Uploading or publishing is never automatic.</p>
      <Section title="Install" commands={INSTALL} />
      <Section title="Run" commands={RUN} />
      <Section title="Troubleshoot" commands={TROUBLESHOOT} />
      <h2>Author a task</h2>
      <p>
        See the task authoring template in the implementation spec (section 26): user impact, symptom, public
        contract, baseline defect, reference, alternatives, fixtures, evaluator, counterexamples, compute needs,
        license, provenance and scoring risks. Independent review is required before a task is admitted.
      </p>
    </section>
  );
}
