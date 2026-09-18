import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { Results } from "./Results";
import { Compare } from "./Compare";
import { EntrantProfile } from "./EntrantProfile";
import { ReleaseDetail } from "./ReleaseDetail";
import { renderWithProviders } from "../test/renderWithProviders";
import { mockApi } from "../test/mockApi";

const notice = "This publication is non-ranking; descriptive evidence only, not a ranked result or calculated comparison.";
const data = {
  id: "pub-n", campaign_id: "camp", snapshot_digest: "digest", status: "published",
  publication_class: "non_ranked", notice, supersedes_id: null,
  created_at: "2026-01-01T00:00:00Z", cohort_digest: null, frozen_tasks: [], frozen_entrants: [],
  snapshot: { schema_version: "aieb.analysis/v1", per_task: {}, per_entrant: { a: 1, b: 0.5 },
    complete_for_rank: false, suite_rate: 0.75, limitations: [] },
};
function mockPublication() {
  return { "/v1/releases": { data: { items: [], next_cursor: null } },
    "/v1/publications/{publication_id}/results": { data } };
}

describe("Non-ranking public consumers", () => {
  it("labels directly selected results even when not in the releases list", async () => {
    mockApi(mockPublication());
    renderWithProviders(<Results />, { route: "/results?publication=pub-n", path: "/results" });
    expect(await screen.findByText(notice)).toBeInTheDocument();
    expect(screen.getByText("Non-ranking — descriptive evidence only")).toBeInTheDocument();
  });
  it("labels an explicit historical release", async () => {
    mockApi(mockPublication());
    renderWithProviders(<ReleaseDetail />, { route: "/releases/pub-n", path: "/releases/:publicationId" });
    expect(await screen.findByText(notice)).toBeInTheDocument();
    expect(screen.getByText("Non-ranking — descriptive evidence only")).toBeInTheDocument();
  });
  it("labels entrant history without disguising descriptive rates as ranked results", async () => {
    mockApi({
      "/v1/entrants/by-slug/{slug}": { data: { id: "a", manifest: { id: "a", agent_version: "1", track: "agents",
        engineer_model: { requested_model: "fixture" }, capabilities: [] } } },
      "/v1/entrants/by-slug/{slug}/results": { data: [{ publication_id: "pub-n", campaign_id: "camp",
        status: "published", publication_class: "non_ranked", notice, created_at: data.created_at, aggregate_rate: 1 }] },
    });
    renderWithProviders(<EntrantProfile />, { route: "/entrants/a", path: "/entrants/:slug" });
    expect(await screen.findByText(notice)).toBeInTheDocument();
    expect(screen.getByText("Non-ranking — descriptive evidence only")).toBeInTheDocument();
  });
  it("shows descriptive panels and no rate-delta table for non-ranking comparisons", async () => {
    mockApi({ ...mockPublication(), "/v1/comparisons": { data: {
      publication_id: "pub-n", cohort_comparable: false,
      non_comparable_reason: "A selected publication is non-ranking; no calculated winners or rate deltas.",
      entrants: ["a", "b"].map(entrant_id => ({ entrant_id, publication_id: "pub-n", eligible: true,
        aggregate: 1, publication_class: "non_ranked", notice })), task_rate_deltas: null,
    } } });
    renderWithProviders(<Compare />, { route: "/compare?publication=pub-n&entrants=a,b", path: "/compare" });
    expect(await screen.findByText(/no calculated winners or rate deltas/)).toBeInTheDocument();
    expect(screen.getAllByText(notice)).toHaveLength(2);
    expect(screen.queryByRole("heading", { name: "Per-task rate deltas" })).not.toBeInTheDocument();
  });
});
