import { describe, expect, it } from "vitest";
import axe from "axe-core";
import { cleanup, render, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { Layout } from "../components/Layout";
import { Home } from "./Home";
import { Results } from "./Results";
import { Compare } from "./Compare";
import { EntrantProfile } from "./EntrantProfile";
import { TaskCatalog } from "./TaskCatalog";
import { TaskDetail } from "./TaskDetail";
import { RunEvidence } from "./RunEvidence";
import { Methodology } from "./Methodology";
import { ReleasesList } from "./ReleasesList";
import { ReleaseDetail } from "./ReleaseDetail";
import { Corrections } from "./Corrections";
import { RunLocally } from "./RunLocally";
import { NotFound } from "./NotFound";
import { mockApi } from "../test/mockApi";

/** Structural accessibility checks (spec: keyboard navigation, visible
 * focus, semantic tables, accessible labels) against the complete site
 * shell and every public route. jsdom cannot calculate rendered colors, so
 * color contrast is covered separately in real-browser visual verification. */
async function checkAccessibility(container: HTMLElement) {
  const results = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
  expect(results.violations, JSON.stringify(results.violations, null, 2)).toHaveLength(0);
}

function renderPage(route: string, path: string, page: ReactNode) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[route]}>
        <Routes>
          <Route element={<Layout />}><Route path={path} element={page} /></Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const noResults = { data: { items: [], next_cursor: null } };
const notFound = { status: 404, error: { error: { code: "not_found", message: "not found", request_id: "a11y", field_errors: {}, retryable: false } } };
const forbidden = { status: 403, error: { error: { code: "forbidden", message: "forbidden", request_id: "a11y", field_errors: {}, retryable: false } } };

describe("full-page accessibility", () => {
  it("has no structural axe violations on every public route", async () => {
    const pages = [
      { route: "/", path: "/", page: <Home />, api: { "/v1/releases": noResults } },
      { route: "/results", path: "/results", page: <Results />, api: { "/v1/releases": noResults } },
      { route: "/compare", path: "/compare", page: <Compare />, api: { "/v1/releases": noResults } },
      { route: "/entrants/demo", path: "/entrants/:slug", page: <EntrantProfile />, api: {
        "/v1/entrants/by-slug/{slug}": notFound, "/v1/entrants/by-slug/{slug}/results": { data: [] },
      } },
      { route: "/tasks", path: "/tasks", page: <TaskCatalog />, api: { "/v1/tasks": noResults } },
      { route: "/tasks/demo/v1", path: "/tasks/:slug/:version", page: <TaskDetail />, api: {
        "/v1/tasks/{slug}/revisions/{version}": notFound,
      } },
      { route: "/runs/demo", path: "/runs/:trialId", page: <RunEvidence />, api: {
        "/v1/trials/{trial_id}": forbidden, "/v1/public/trials/{trial_id}": notFound,
      } },
      { route: "/methodology", path: "/methodology", page: <Methodology />, api: { "/v1/methodology": { data: [] } } },
      { route: "/releases", path: "/releases", page: <ReleasesList />, api: { "/v1/releases": noResults } },
      { route: "/releases/demo", path: "/releases/:publicationId", page: <ReleaseDetail />, api: {
        "/v1/publications/{publication_id}/results": notFound,
      } },
      { route: "/corrections", path: "/corrections", page: <Corrections />, api: { "/v1/corrections": noResults } },
      { route: "/docs", path: "/docs", page: <RunLocally />, api: {} },
      { route: "/missing", path: "*", page: <NotFound />, api: {} },
    ];

    for (const item of pages) {
      mockApi(item.api);
      const { container } = renderPage(item.route, item.path, item.page);
      await waitFor(() => expect(container.querySelector('[role="status"]')?.textContent ?? "ready").not.toContain("Loading"));
      await checkAccessibility(container);
      cleanup();
    }
  }, 30000);
});
