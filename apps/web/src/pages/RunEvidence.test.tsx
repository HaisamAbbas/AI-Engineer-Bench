import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { RunEvidence } from "./RunEvidence";
import { renderWithProviders } from "../test/renderWithProviders";
import { mockApi } from "../test/mockApi";

describe("RunEvidence", () => {
  it("shows an honest authorization-required state for a 401/403 (no public redacted view exists yet)", async () => {
    mockApi({
      "/v1/trials/{trial_id}": {
        status: 403,
        error: { error: { code: "forbidden", message: "requires one of roles: operator, reviewer, administrator", request_id: "r1", field_errors: {}, retryable: false } },
      },
    });
    renderWithProviders(<RunEvidence />, { route: "/runs/trial-1", path: "/runs/:trialId" });
    await waitFor(() => expect(screen.getByText(/requires operator, reviewer, or administrator authorization/i)).toBeInTheDocument());
  });

  it("shows an honest not-found state for a missing run", async () => {
    mockApi({
      "/v1/trials/{trial_id}": {
        status: 404,
        error: { error: { code: "not_found", message: "not found", request_id: "r2", field_errors: {}, retryable: false } },
      },
    });
    renderWithProviders(<RunEvidence />, { route: "/runs/trial-2", path: "/runs/:trialId" });
    await waitFor(() => expect(screen.getByText(/No run with this ID exists/i)).toBeInTheDocument());
  });

  it("renders a terminal run's phase/status as plain text", async () => {
    mockApi({
      "/v1/trials/{trial_id}": {
        data: {
          id: "trial-3",
          task_revision_id: "task-rev-1",
          entrant_revision_id: "entrant-rev-1",
          repetition: 0,
          latest_attempt: { number: 1, phase: "terminal", terminal_status: "pass" },
        },
      },
    });
    renderWithProviders(<RunEvidence />, { route: "/runs/trial-3", path: "/runs/:trialId" });
    await waitFor(() => expect(screen.getByText("pass")).toBeInTheDocument());
    expect(screen.getByText(/Full evidence tabs/i)).toBeInTheDocument();
  });
});
