import { describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Results } from "./Results";
import { renderWithProviders } from "../test/renderWithProviders";
import { mockApi } from "../test/mockApi";

const SNAPSHOT_WITH_FIVE_ENTRANTS = {
  schema_version: "aieb.analysis/v1",
  per_task: {},
  per_entrant: { a: 1.0, b: 0.5, c: 0.0, d: null, e: 0.75 },
  per_category: null,
  complete_for_rank: true,
  suite_rate: 0.5,
  cost_per_resolution: 3.5,
  total_campaign_cost_usd: null,
  verifier_cost_total_usd: null,
  successful_engineering_median_seconds: 120,
  deadline_rate: null,
  infrastructure_attrition: null,
  limitations: [],
};

function mockOnePublication(overrides: { status?: string; snapshot?: unknown } = {}) {
  mockApi({
    "/v1/releases": {
      data: {
        items: [{ id: "pub-1", campaign_id: "camp-1", snapshot_digest: "d", status: "published", created_at: "2026-01-01T00:00:00Z" }],
        next_cursor: null,
      },
    },
    "/v1/publications/{publication_id}/results": {
      data: {
        id: "pub-1",
        campaign_id: "camp-1",
        snapshot_digest: "d",
        status: overrides.status ?? "published",
        supersedes_id: null,
        created_at: "2026-01-01T00:00:00Z",
        cohort_digest: "cohort-a",
        snapshot: overrides.snapshot ?? SNAPSHOT_WITH_FIVE_ENTRANTS,
        ...(overrides.status === "withdrawn"
          ? { notice: "this snapshot has been withdrawn; it remains addressable but is not canonical" }
          : {}),
      },
    },
  });
}

describe("Results", () => {
  it("shows an honest empty-cohort state when no releases exist", async () => {
    mockApi({ "/v1/releases": { data: { items: [], next_cursor: null } } });
    renderWithProviders(<Results />);
    await waitFor(() => expect(screen.getByText(/No published cohort yet/i)).toBeInTheDocument());
  });

  it("displays a withdrawn-snapshot notice", async () => {
    mockOnePublication({ status: "withdrawn" });
    renderWithProviders(<Results />);
    await waitFor(() => {
      const alerts = screen.getAllByRole("alert");
      expect(alerts.some((alert) => /withdrawn/i.test(alert.textContent ?? ""))).toBe(true);
    });
  });

  it("sorts null rates last, not as a fabricated zero, and shows Unknown for them", async () => {
    mockOnePublication();
    renderWithProviders(<Results />);
    await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "Rate" }));
    const rows = screen.getAllByRole("row").slice(1); // drop header row
    const lastRow = rows[rows.length - 1];
    expect(within(lastRow).getByText("Unknown")).toBeInTheDocument();
  });

  it("limits entrant selection to 4 and disables further checkboxes", async () => {
    mockOnePublication();
    renderWithProviders(<Results />);
    await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
    const checkboxes = screen.getAllByRole("checkbox");
    expect(checkboxes).toHaveLength(5);
    for (const checkbox of checkboxes.slice(0, 4)) {
      await userEvent.click(checkbox);
    }
    expect(checkboxes[4]).toBeDisabled();
    expect(screen.getByRole("link", { name: /Compare 4 selected entrants/i })).toBeInTheDocument();
  });
});
