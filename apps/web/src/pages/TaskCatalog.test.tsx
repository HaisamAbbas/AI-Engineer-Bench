import { describe, expect, it } from "vitest";
import { MemoryRouter, Routes, Route, useLocation } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TaskCatalog } from "./TaskCatalog";
import { renderWithProviders } from "../test/renderWithProviders";
import { mockApi } from "../test/mockApi";

function LocationDisplay() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname + location.search}</div>;
}

describe("TaskCatalog", () => {
  it("shows an empty-search state when a filter matches nothing", async () => {
    mockApi({
      "/v1/tasks": {
        data: {
          items: [
            { id: "1", slug: "rag.document-freshness", version: "0.1.0", family_id: "knowledge-service-a", category: "rag", activity: "repair", created_at: "2026-01-01T00:00:00Z" },
          ],
          next_cursor: null,
        },
      },
    });
    renderWithProviders(<TaskCatalog />);
    await waitFor(() => expect(screen.getByText("rag.document-freshness")).toBeInTheDocument());
    await userEvent.type(screen.getByLabelText("Search"), "no-such-task");
    expect(screen.getByText(/No tasks match this search/i)).toBeInTheDocument();
  });

  it("shows an empty-catalog state when there are no tasks at all", async () => {
    mockApi({ "/v1/tasks": { data: { items: [], next_cursor: null } } });
    renderWithProviders(<TaskCatalog />);
    await waitFor(() => expect(screen.getByText(/No tasks are catalogued yet/i)).toBeInTheDocument());
  });

  it("encodes the category filter in the URL so the view is shareable (spec 4: URL-backed filters)", async () => {
    mockApi({
      "/v1/tasks": {
        data: {
          items: [
            { id: "1", slug: "rag.document-freshness", version: "0.1.0", family_id: "knowledge-service-a", category: "rag", activity: "repair", created_at: "2026-01-01T00:00:00Z" },
            { id: "2", slug: "tool.false-completion", version: "0.1.0", family_id: "assistant-service-e", category: "tool_app", activity: "repair", created_at: "2026-01-01T00:00:00Z" },
          ],
          next_cursor: null,
        },
      },
    });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/tasks"]}>
          <Routes>
            <Route
              path="/tasks"
              element={
                <>
                  <TaskCatalog />
                  <LocationDisplay />
                </>
              }
            />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getByText("rag.document-freshness")).toBeInTheDocument());
    await userEvent.selectOptions(screen.getByLabelText("Category"), "tool_app");
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("/tasks?category=tool_app"));
  });
});
