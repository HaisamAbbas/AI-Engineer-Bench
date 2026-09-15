import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ReleasesList } from "./ReleasesList";
import { renderWithProviders } from "../test/renderWithProviders";
import { mockApi } from "../test/mockApi";

describe("ReleasesList", () => {
  it("shows an empty state when nothing has been published", async () => {
    mockApi({ "/v1/releases": { data: { items: [], next_cursor: null } } });
    renderWithProviders(<ReleasesList />, { route: "/releases", path: "/releases" });
    await waitFor(() => expect(screen.getByText(/No releases have been published yet/i)).toBeInTheDocument());
  });

  it("exposes a Next page control that follows next_cursor", async () => {
    mockApi({
      "/v1/releases": (params) => {
        const cursor = (params as { query?: { cursor?: string } } | undefined)?.query?.cursor;
        if (cursor === "cursor-2") {
          return { data: { items: [{ id: "pub-2", campaign_id: "camp-2", snapshot_digest: "d2", status: "published", created_at: "2026-01-02T00:00:00Z" }], next_cursor: null } };
        }
        return { data: { items: [{ id: "pub-1", campaign_id: "camp-1", snapshot_digest: "d1", status: "published", created_at: "2026-01-01T00:00:00Z" }], next_cursor: "cursor-2" } };
      },
    });
    renderWithProviders(<ReleasesList />, { route: "/releases", path: "/releases" });
    await waitFor(() => expect(screen.getByText("pub-1")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: /Next page/i }));
    await waitFor(() => expect(screen.getByText("pub-2")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: /Next page/i })).not.toBeInTheDocument();
  });
});
