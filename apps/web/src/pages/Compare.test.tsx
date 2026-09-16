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

  it("renders per-task rate deltas when cohort-comparable", async () => {
    mockApi({
      "/v1/publications/{publication_id}/results": {
        data: {
          id: "pub-1", campaign_id: "camp-1", snapshot_digest: "d", status: "published", supersedes_id: null,
          created_at: "2026-01-01T00:00:00Z", cohort_digest: "cohort-a", frozen_tasks: [], frozen_entrants: [],
          snapshot: { schema_version: "aieb.analysis/v1", per_task: {}, per_entrant: {}, per_category: null, complete_for_rank: true, suite_rate: null, cost_per_resolution: null, total_campaign_cost_usd: null, verifier_cost_total_usd: null, successful_engineering_median_seconds: null, deadline_rate: null, infrastructure_attrition: null, limitations: [] },
        },
      },
      "/v1/comparisons": {
        data: {
          publication_id: "pub-1",
          cohort_comparable: true,
          non_comparable_reason: null,
          entrants: [
            { entrant_id: "a", publication_id: "pub-1", eligible: true, aggregate: 1.0 },
            { entrant_id: "b", publication_id: "pub-1", eligible: true, aggregate: 0.5 },
          ],
          task_rate_deltas: {
            "a|b": [{ task_id: "task-1", left_rate: 1.0, right_rate: 0.5, difference: 0.5 }],
          },
        },
      },
    });
    renderWithProviders(<Compare />, { route: "/compare?publication=pub-1&entrants=a,b", path: "/compare" });
    await waitFor(() => expect(screen.getByText("Per-task rate deltas")).toBeInTheDocument());
    expect(screen.getByText("task-1")).toBeInTheDocument();
    expect(screen.getByText("+50.0 pp")).toBeInTheDocument();
  });

  it("shows each entrant's exact configuration and per-entrant cost/time, not only an aggregate rate", async () => {
    // Review finding #4: Compare previously showed only "Rate: X" per
    // entrant. This asserts the panel now also shows the entrant's real
    // configuration (version, model, capabilities) and per-entrant
    // cost/time/coverage - the same authoritative fields Results.tsx shows.
    mockApi({
      "/v1/publications/{publication_id}/results": {
        data: {
          id: "pub-1", campaign_id: "camp-1", snapshot_digest: "d", status: "published", supersedes_id: null,
          created_at: "2026-01-01T00:00:00Z", cohort_digest: "cohort-a",
          // Total tasks now comes from the frozen plan (3 tasks), not the
          // snapshot's observed-cell count - resolved (2) / frozen (3) = "2/3".
          frozen_tasks: [
            { slug: "t1", version: "0.1.0", family_id: "f", category: "rag" },
            { slug: "t2", version: "0.1.0", family_id: "f", category: "rag" },
            { slug: "t3", version: "0.1.0", family_id: "f", category: "rag" },
          ],
          frozen_entrants: [{ slug: "a", version: "1.0.0" }, { slug: "b", version: "1.0.0" }],
          snapshot: {
            schema_version: "aieb.analysis/v1", required_repetitions: null, per_task: {}, per_entrant: { a: 1.0, b: 0.5 },
            per_category: null, complete_for_rank: true, suite_rate: null, cost_per_resolution: null,
            total_campaign_cost_usd: null, verifier_cost_total_usd: null, successful_engineering_median_seconds: null,
            deadline_rate: null, infrastructure_attrition: null,
            per_entrant_valid_trials: {}, per_entrant_resolved_tasks: { a: 2 }, per_entrant_total_tasks: { a: 3 },
            per_entrant_cost_per_resolution: { a: 4.5 }, per_entrant_verifier_cost_usd: {},
            per_entrant_median_engineering_seconds: { a: 90 }, per_entrant_deadline_rate: { a: 0 },
            per_entrant_infrastructure_attrition: {}, limitations: [],
          },
        },
      },
      "/v1/publications/{publication_id}/entrants/{slug}": ({ path }: any) => ({
        data: {
          manifest: {
            schema_version: "aieb.entrant/v1", id: path.slug, track: "agents", agent_implementation: "demo",
            agent_version: "3.2.1", engineer_model: { provider_class: "demo", requested_model: "demo-model", settings_digest: "a".repeat(64) },
            prompt_digest: "b".repeat(64), tools_digest: "c".repeat(64), capabilities: ["cpu-fixture-standard-v1"],
            credential_ref_type: "broker",
          },
        },
      }),
      "/v1/comparisons": {
        data: {
          publication_id: "pub-1", cohort_comparable: true, non_comparable_reason: null,
          entrants: [
            { entrant_id: "a", publication_id: "pub-1", eligible: true, aggregate: 1.0 },
            { entrant_id: "b", publication_id: "pub-1", eligible: true, aggregate: 0.5 },
          ],
          task_rate_deltas: { "a|b": [] },
        },
      },
    });
    renderWithProviders(<Compare />, { route: "/compare?publication=pub-1&entrants=a,b", path: "/compare" });
    await waitFor(() => expect(screen.getAllByText("3.2.1").length).toBeGreaterThan(0));
    expect(screen.getAllByText("demo-model").length).toBeGreaterThan(0);
    expect(screen.getAllByText("cpu-fixture-standard-v1").length).toBeGreaterThan(0);
    expect(screen.getByText("2/3")).toBeInTheDocument();
    expect(screen.getByText("$4.50")).toBeInTheDocument();
  });

  it("shows a non-comparable notice and no paired differences across incompatible cohorts", async () => {
    mockApi({
      "/v1/publications/{publication_id}/results": {
        data: {
          id: "pub-1", campaign_id: "camp-1", snapshot_digest: "d", status: "published", supersedes_id: null,
          created_at: "2026-01-01T00:00:00Z", cohort_digest: "cohort-a", frozen_tasks: [], frozen_entrants: [],
          snapshot: { schema_version: "aieb.analysis/v1", per_task: {}, per_entrant: {}, per_category: null, complete_for_rank: true, suite_rate: null, cost_per_resolution: null, total_campaign_cost_usd: null, verifier_cost_total_usd: null, successful_engineering_median_seconds: null, deadline_rate: null, infrastructure_attrition: null, limitations: [] },
        },
      },
      "/v1/comparisons": {
        data: {
          publication_id: "pub-1",
          cohort_comparable: false,
          non_comparable_reason: "entrants come from publications with different (or unresolvable) frozen cohorts; paired statistics are not meaningful across different cohorts",
          entrants: [
            { entrant_id: "a", publication_id: "pub-1", eligible: true, aggregate: 1.0 },
            { entrant_id: "b", publication_id: "pub-1", eligible: true, aggregate: 0.5 },
          ],
          task_rate_deltas: null,
        },
      },
    });
    renderWithProviders(<Compare />, { route: "/compare?publication=pub-1&entrants=a,b", path: "/compare" });
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/not cohort-comparable/i));
    expect(screen.queryByText("Per-task rate deltas")).not.toBeInTheDocument();
    // Each entrant still shown separately - never a fabricated winner.
    expect(screen.getByText("a")).toBeInTheDocument();
    expect(screen.getByText("b")).toBeInTheDocument();
  });

  it("keeps two panels distinct when the same slug is compared across two releases", async () => {
    // Review finding #4, third pass: the same slug from two publications must
    // render as two panels each showing its OWN publication's aggregate, not
    // one overwriting the other.
    const resultsData = {
      id: "pub", campaign_id: "camp", snapshot_digest: "d", status: "published", supersedes_id: null,
      created_at: "2026-01-01T00:00:00Z", cohort_digest: "c", frozen_tasks: [], frozen_entrants: [],
      snapshot: { schema_version: "aieb.analysis/v1", per_task: {}, per_entrant: {}, per_category: null, complete_for_rank: true, suite_rate: null, cost_per_resolution: null, total_campaign_cost_usd: null, verifier_cost_total_usd: null, successful_engineering_median_seconds: null, deadline_rate: null, infrastructure_attrition: null, limitations: [] },
    };
    mockApi({
      "/v1/publications/{publication_id}/results": { data: resultsData },
      "/v1/publications/{publication_id}/entrants/{slug}": ({ path }: any) => ({
        data: {
          manifest: {
            schema_version: "aieb.entrant/v1", id: path.slug, track: "agents", agent_implementation: "demo",
            agent_version: "1.0.0", engineer_model: { provider_class: "demo", requested_model: "m", settings_digest: "a".repeat(64) },
            prompt_digest: "b".repeat(64), tools_digest: "c".repeat(64), capabilities: ["cap"], credential_ref_type: "broker",
          },
        },
      }),
      "/v1/comparisons": {
        data: {
          publication_id: "pub-a",
          cohort_comparable: false,
          non_comparable_reason: "entrants come from different publications (a cross-release comparison)",
          entrants: [
            { entrant_id: "agent-a", publication_id: "pub-a", eligible: true, aggregate: 1.0 },
            { entrant_id: "agent-a", publication_id: "pub-b", eligible: true, aggregate: 0.3 },
          ],
          task_rate_deltas: null,
        },
      },
    });
    renderWithProviders(<Compare />, {
      route: "/compare?entrants=agent-a,agent-a&entrant_publications=pub-a,pub-b",
      path: "/compare",
    });
    // Two panels, both for agent-a, each with its own aggregate (100% and 30%).
    await waitFor(() => expect(screen.getAllByText("agent-a").length).toBe(2));
    expect(screen.getByText("100.0%")).toBeInTheDocument();
    expect(screen.getByText("30.0%")).toBeInTheDocument();
  });
});
