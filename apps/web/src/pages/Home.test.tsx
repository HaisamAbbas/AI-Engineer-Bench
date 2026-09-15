import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Home } from "./Home";
import { renderWithProviders } from "../test/renderWithProviders";
import { mockApi } from "../test/mockApi";
import { api } from "../api/client";

describe("Home", () => {
  it("shows an honest empty state when no publication exists yet - never a placeholder score", async () => {
    mockApi({ "/v1/releases": { data: { items: [], next_cursor: null } } });
    renderWithProviders(<Home />);
    await waitFor(() => expect(screen.getByText(/No publication has been released yet/i)).toBeInTheDocument());
    expect(screen.queryByText(/%$/)).not.toBeInTheDocument();
  });

  it("shows an error state with a working Retry that re-fetches on success", async () => {
    const spy = vi.spyOn(api, "GET");
    spy.mockResolvedValueOnce({
      data: undefined,
      error: { error: { code: "error", message: "network error", request_id: "r1", field_errors: {}, retryable: true } },
      response: new Response(null, { status: 500 }),
    } as never);
    renderWithProviders(<Home />);
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());

    spy.mockResolvedValueOnce({ data: { items: [], next_cursor: null }, error: undefined, response: new Response(null, { status: 200 }) } as never);
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(screen.getByText(/No publication has been released yet/i)).toBeInTheDocument());
  });
});
