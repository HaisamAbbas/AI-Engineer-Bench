import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { EntrantProfile } from "./EntrantProfile";
import { renderWithProviders } from "../test/renderWithProviders";
import { mockApi } from "../test/mockApi";

const ENTRANT_ROUTE = {
  "/v1/entrants/by-slug/{slug}": {
    data: {
      id: "11111111-1111-1111-1111-111111111111",
      manifest: {
        schema_version: "aieb.entrant/v1", id: "agent-a", track: "agents", agent_implementation: "demo",
        agent_version: "1.0.0", engineer_model: { provider_class: "demo", requested_model: "demo-model", reported_model: null, settings_digest: "a".repeat(64) },
        prompt_digest: "b".repeat(64), tools_digest: "c".repeat(64), capabilities: ["cpu-fixture-standard-v1"],
        credential_ref_type: "broker",
      },
    },
  },
};

describe("EntrantProfile", () => {
  it("shows an empty state when the entrant appears in no published release", async () => {
    mockApi({ ...ENTRANT_ROUTE, "/v1/entrants/by-slug/{slug}/results": { data: [] } });
    renderWithProviders(<EntrantProfile />, { route: "/entrants/agent-a", path: "/entrants/:slug" });
    await waitFor(() => expect(screen.getByText("agent-a")).toBeInTheDocument());
    expect(screen.getByText(/does not appear in any published release yet/i)).toBeInTheDocument();
  });

  it("lists every release the entrant appears in", async () => {
    mockApi({
      ...ENTRANT_ROUTE,
      "/v1/entrants/by-slug/{slug}/results": {
        data: [
          { publication_id: "pub-1", campaign_id: "camp-1", status: "published", created_at: "2026-01-01T00:00:00Z", aggregate_rate: 0.9 },
        ],
      },
    });
    renderWithProviders(<EntrantProfile />, { route: "/entrants/agent-a", path: "/entrants/:slug" });
    await waitFor(() => expect(screen.getByText("pub-1")).toBeInTheDocument());
    expect(screen.getByText("90.0%")).toBeInTheDocument();
  });
});
