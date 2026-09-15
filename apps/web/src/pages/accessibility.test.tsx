import { describe, expect, it } from "vitest";
import axe from "axe-core";
import { waitFor } from "@testing-library/react";
import { Home } from "./Home";
import { TaskCatalog } from "./TaskCatalog";
import { renderWithProviders } from "../test/renderWithProviders";
import { mockApi } from "../test/mockApi";

/** Structural accessibility checks (spec: "keyboard navigation, visible
 * focus, semantic tables, accessible labels") via axe-core against the
 * actual rendered DOM - not a checklist read off the code. jsdom cannot
 * evaluate real color contrast, so axe's color-contrast rule is disabled
 * here; every other rule (label association, table headers, landmark
 * structure, ARIA usage) runs for real. */
async function checkAccessibility(container: HTMLElement) {
  const results = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
  expect(results.violations, JSON.stringify(results.violations, null, 2)).toHaveLength(0);
}

describe("accessibility", () => {
  it("Home has no structural a11y violations", async () => {
    mockApi({ "/v1/releases": { data: { items: [], next_cursor: null } } });
    const { container } = renderWithProviders(<Home />);
    await waitFor(() => expect(container.textContent).not.toBe(""));
    await checkAccessibility(container);
  });

  it("TaskCatalog has no structural a11y violations", async () => {
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
    const { container } = renderWithProviders(<TaskCatalog />);
    await waitFor(() => expect(container.textContent).not.toBe(""));
    await checkAccessibility(container);
  });
});
