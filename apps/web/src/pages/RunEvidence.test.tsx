import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { RunEvidence } from "./RunEvidence";
import { renderWithProviders } from "../test/renderWithProviders";
import { mockApi } from "../test/mockApi";

const notFound = { error: { code: "not_found", message: "not found", request_id: "r1", field_errors: {}, retryable: false } };

describe("RunEvidence", () => {
  it("renders a published redacted run after private access is denied", async () => {
    mockApi({
      "/v1/trials/{trial_id}": {
        status: 403,
        error: { error: { code: "forbidden", message: "requires authorization", request_id: "r1", field_errors: {}, retryable: false } },
      },
      "/v1/public/trials/{trial_id}": {
        data: {
          trial_id: "trial-1", publication_id: "publication-1", task_id: "task-a", task_version: "1.0.0",
          entrant_id: "agent-a", entrant_version: "2.0.0", repetition: 0,
          attempt: { number: 1, phase: "terminal", terminal_status: "pass" }, verdict: "pass",
          checks: [{ requirement_id: "ready", passed: true }],
        },
      },
    });
    renderWithProviders(<RunEvidence />, { route: "/runs/trial-1", path: "/runs/:trialId" });
    await waitFor(() => expect(screen.getByText(/Public redacted evidence/)).toBeInTheDocument());
    expect(screen.getByText("task-a v1.0.0")).toBeInTheDocument();
    expect(screen.getByText("ready: Pass")).toBeInTheDocument();
    expect(screen.queryByText(/Candidate diff/i)).not.toBeInTheDocument();
  });

  it("renders authorized candidate output and stored unified diffs as text", async () => {
    const hostile = '<img src=x onerror="window.__pwned=true">\x1b[31mred\x1b[0m';
    mockApi({
      "/v1/trials/{trial_id}": {
        data: {
          id: "trial-2", task_revision_id: "task-rev-1", entrant_revision_id: "entrant-rev-1", repetition: 0,
          latest_attempt: { number: 1, phase: "terminal", terminal_status: "pass" },
          verdict: "pass", checks: { ready: true }, diagnostics: { result: hostile },
          engineering_stdout: hostile, engineering_stderr: "", engineering_logs_truncated: false,
          diffs: [{ path: "src/app.py", operation: "modify", unified_diff: hostile, binary: false, truncated: false, baseline_available: true }],
        },
      },
    });
    (window as unknown as { __pwned?: boolean }).__pwned = false;
    renderWithProviders(<RunEvidence />, { route: "/runs/trial-2", path: "/runs/:trialId" });
    await waitFor(() => expect(screen.getByRole("heading", { name: "Candidate diff" })).toBeInTheDocument());
    expect(screen.getAllByText(hostile)).toHaveLength(3);
    expect(document.querySelector("img")).toBeNull();
    expect((window as unknown as { __pwned?: boolean }).__pwned).toBe(false);
  });

  it("shows not found when neither private nor published evidence exists", async () => {
    mockApi({ "/v1/trials/{trial_id}": { status: 404, error: notFound } });
    renderWithProviders(<RunEvidence />, { route: "/runs/trial-3", path: "/runs/:trialId" });
    await waitFor(() => expect(screen.getByText(/No public or authorized run/)).toBeInTheDocument());
  });
});
