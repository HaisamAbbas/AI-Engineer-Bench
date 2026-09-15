import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { Corrections } from "./Corrections";
import { renderWithProviders } from "../test/renderWithProviders";
import { mockApi } from "../test/mockApi";

describe("Corrections", () => {
  it("shows an honest empty state when no corrections have been made", async () => {
    mockApi({ "/v1/corrections": { data: { items: [], next_cursor: null } } });
    renderWithProviders(<Corrections />, { route: "/corrections", path: "/corrections" });
    await waitFor(() => expect(screen.getByText(/No corrections have been made/i)).toBeInTheDocument());
  });

  it("lists a correction and links to what it supersedes", async () => {
    mockApi({
      "/v1/corrections": {
        data: {
          items: [{ id: "pub-2", campaign_id: "camp-1", status: "published", supersedes_id: "pub-1", created_at: "2026-01-02T00:00:00Z" }],
          next_cursor: null,
        },
      },
    });
    renderWithProviders(<Corrections />, { route: "/corrections", path: "/corrections" });
    await waitFor(() => expect(screen.getByText("pub-2")).toBeInTheDocument());
    expect(screen.getByRole("link", { name: "pub-1" })).toHaveAttribute("href", "/releases/pub-1");
  });
});
