import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { Compare } from "./Compare";
import { renderWithProviders } from "../test/renderWithProviders";
import { mockApi } from "../test/mockApi";

describe("Compare", () => {
  it("shows an honest empty state with fewer than 2 entrants selected", async () => {
    renderWithProviders(<Compare />, { route: "/compare", path: "/compare" });
    expect(screen.getByText(/Select 2 to 4 entrants to compare/i)).toBeInTheDocument();
  });

  it("renders paired task differences when cohort-comparable", async () => {
    mockApi({
      "/v1/publications/{publication_id}/results": {
        data: {
          id: "pub-1", campaign_id: "camp-1", snapshot_digest: "d", status: "published", supersedes_id: null,
          created_at: "2026-01-01T00:00:00Z", cohort_digest: "cohort-a",
          snapshot: { schema_version: "aieb.analysis/v1", per_task: {}, per_entrant: {}, per_category: null, complete_for_rank: true, suite_rate: null, cost_per_resolution: null, total_campaign_cost_usd: null, verifier_cost_total_usd: null, successful_engineering_median_seconds: null, deadline_rate: null, infrastructure_attrition: null, limitations: [] },
        },
      },
      "/v1/comparisons": {
        data: {
          publication_id: "pub-1",
          cohort_comparable: true,
          non_comparable_reason: null,
          entrants: { a: { eligible: true, aggregate: 1.0 }, b: { eligible: true, aggregate: 0.5 } },
          paired_differences: {
            "a|b": [{ task_id: "task-1", left_rate: 1.0, right_rate: 0.5, difference: 0.5 }],
          },
        },
      },
    });
    renderWithProviders(<Compare />, { route: "/compare?publication=pub-1&entrants=a,b", path: "/compare" });
    await waitFor(() => expect(screen.getByText("Paired task outcomes")).toBeInTheDocument());
    expect(screen.getByText("task-1")).toBeInTheDocument();
    expect(screen.getByText("+50.0 pp")).toBeInTheDocument();
  });

  it("shows a non-comparable notice and no paired differences across incompatible cohorts", async () => {
    mockApi({
      "/v1/publications/{publication_id}/results": {
        data: {
          id: "pub-1", campaign_id: "camp-1", snapshot_digest: "d", status: "published", supersedes_id: null,
          created_at: "2026-01-01T00:00:00Z", cohort_digest: "cohort-a",
          snapshot: { schema_version: "aieb.analysis/v1", per_task: {}, per_entrant: {}, per_category: null, complete_for_rank: true, suite_rate: null, cost_per_resolution: null, total_campaign_cost_usd: null, verifier_cost_total_usd: null, successful_engineering_median_seconds: null, deadline_rate: null, infrastructure_attrition: null, limitations: [] },
        },
      },
      "/v1/comparisons": {
        data: {
          publication_id: "pub-1",
          cohort_comparable: false,
          non_comparable_reason: "entrants come from publications with different (or unresolvable) frozen cohorts; paired statistics are not meaningful across different cohorts",
          entrants: { a: { eligible: true, aggregate: 1.0 }, b: { eligible: true, aggregate: 0.5 } },
          paired_differences: null,
        },
      },
    });
    renderWithProviders(<Compare />, { route: "/compare?publication=pub-1&entrants=a,b", path: "/compare" });
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/not cohort-comparable/i));
    expect(screen.queryByText("Paired task outcomes")).not.toBeInTheDocument();
    // Each entrant still shown separately - never a fabricated winner.
    expect(screen.getByText("a")).toBeInTheDocument();
    expect(screen.getByText("b")).toBeInTheDocument();
  });
});
