import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { ReleaseDetail } from "./ReleaseDetail";
import { renderWithProviders } from "../test/renderWithProviders";
import { mockApi } from "../test/mockApi";

describe("ReleaseDetail", () => {
  it("shows a superseded notice and links to the release it supersedes", async () => {
    mockApi({
      "/v1/publications/{publication_id}/results": {
        data: {
          id: "pub-2", campaign_id: "camp-1", snapshot_digest: "d", status: "superseded", supersedes_id: "pub-1",
          created_at: "2026-01-02T00:00:00Z", cohort_digest: "cohort-a",
          cohort: { track: "agents", suite_id: "suite-a", protocol_id: "protocol-a", dependency_mode: "fixture", hardware_class: "cpu-fixture-standard-v1" },
          frozen_tasks: [
            { slug: "task-1", version: "0.1.0", family_id: "family-a", category: "rag" },
            { slug: "task-2", version: "0.1.0", family_id: "family-b", category: "rag" },
          ],
          snapshot: {
            schema_version: "aieb.analysis/v1", per_task: { "task-1:agent-a": { s: 1, n: 1, rate: 1.0, wilson_95: null, all_k: true, pass_power_k: 1.0 } },
            per_entrant: { "agent-a": 1.0 }, per_category: null, complete_for_rank: true, suite_rate: 1.0,
            cost_per_resolution: null, total_campaign_cost_usd: null, verifier_cost_total_usd: null,
            successful_engineering_median_seconds: null, deadline_rate: null, infrastructure_attrition: null, limitations: [],
          },
        },
      },
    });
    renderWithProviders(<ReleaseDetail />, { route: "/releases/pub-2", path: "/releases/:publicationId" });
    await waitFor(() => expect(screen.getByText(/has been superseded/i)).toBeInTheDocument());
    expect(screen.getByRole("link", { name: /release pub-1/i })).toHaveAttribute("href", "/releases/pub-1");
    expect(screen.getByText("cohort-a")).toBeInTheDocument();
    expect(screen.getByText(/suite-a/)).toBeInTheDocument();
    // task-1 has an observation; task-2 has none - review finding #3: a
    // planned task with zero observations must still appear, flagged, not
    // silently vanish because it has no snapshot.per_task cell.
    expect(screen.getByText(/task-1/)).toBeInTheDocument();
    const task2 = screen.getByText(/task-2/).closest("li")!;
    expect(task2).toHaveTextContent("no observations");
  });

  it("shows an honest message when no earlier release is superseded", async () => {
    mockApi({
      "/v1/publications/{publication_id}/results": {
        data: {
          id: "pub-1", campaign_id: "camp-1", snapshot_digest: "d", status: "published", supersedes_id: null,
          created_at: "2026-01-01T00:00:00Z", cohort_digest: null, frozen_tasks: [],
          snapshot: { schema_version: "aieb.analysis/v1", per_task: {}, per_entrant: {}, per_category: null, complete_for_rank: true, suite_rate: null, cost_per_resolution: null, total_campaign_cost_usd: null, verifier_cost_total_usd: null, successful_engineering_median_seconds: null, deadline_rate: null, infrastructure_attrition: null, limitations: [] },
        },
      },
    });
    renderWithProviders(<ReleaseDetail />, { route: "/releases/pub-1", path: "/releases/:publicationId" });
    await waitFor(() => expect(screen.getByText(/does not supersede an earlier one/i)).toBeInTheDocument());
    expect(screen.getByText(/No tasks are recorded/i)).toBeInTheDocument();
  });
});
