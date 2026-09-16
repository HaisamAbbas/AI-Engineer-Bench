import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { Methodology } from "./Methodology";
import { renderWithProviders } from "../test/renderWithProviders";
import { mockApi } from "../test/mockApi";

describe("Methodology", () => {
  it("loads and renders the persisted protocol revision", async () => {
    const version = "protocol-v1";
    const manifest = {
      schema_version: "aieb.protocol/v1", id: version, scoring_digest: "a".repeat(64),
      max_replacements: 2, required_trace_coverage: true, hard_cost_ranking: false,
    };
    mockApi({
      "/v1/methodology": { data: [{ version, scoring_digest: manifest.scoring_digest, created_at: "2026-09-16T00:00:00Z" }] },
      "/v1/methodology/{version}": { data: { version, scoring_digest: manifest.scoring_digest, manifest, created_at: "2026-09-16T00:00:00Z" } },
    });
    renderWithProviders(<Methodology />, { route: "/methodology", path: "/methodology" });
    await waitFor(() => expect(screen.getByText("protocol-v1")).toBeInTheDocument());
    expect(screen.getByText("Required", { selector: "dd" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download this protocol revision" })).toBeInTheDocument();
  });

  it("explains when the registry contains no protocol revisions", async () => {
    mockApi({ "/v1/methodology": { data: [] } });
    renderWithProviders(<Methodology />);
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(/No frozen campaign/));
  });
});
