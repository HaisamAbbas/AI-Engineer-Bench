import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { Home } from "./Home";
import { renderWithProviders } from "../test/renderWithProviders";
import { mockApi } from "../test/mockApi";

describe("Home", () => {
  it("shows an honest empty state when no publication exists yet - never a placeholder score", async () => {
    mockApi({ "/v1/releases": { data: { items: [], next_cursor: null } } });
    renderWithProviders(<Home />);
    await waitFor(() => expect(screen.getByText(/No publication has been released yet/i)).toBeInTheDocument());
    expect(screen.queryByText(/%$/)).not.toBeInTheDocument();
  });
});
