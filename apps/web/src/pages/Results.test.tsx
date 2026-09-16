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
        frozen_tasks: [],
        frozen_entrants: [
          { slug: "a", version: "1.0.0" }, { slug: "b", version: "1.0.0" }, { slug: "c", version: "1.0.0" },
          { slug: "d", version: "1.0.0" }, { slug: "e", version: "1.0.0" },
        ],
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
    const rateCell = within(lastRow).getAllByRole("cell")[0]; // th[row]=Entrant, td[0]=Rate, ...
    expect(rateCell).toHaveTextContent("Unknown");
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

  it("shows a frozen entrant with zero observations as an incomplete row, not omitted", async () => {
    // Review finding #3, third pass: a frozen entrant never observed must
    // still appear (incomplete coverage), sourced from frozen_entrants, not
    // vanish because it has no per_entrant cell.
    mockApi({
      "/v1/releases": {
        data: { items: [{ id: "pub-1", campaign_id: "camp-1", snapshot_digest: "d", status: "published", created_at: "2026-01-01T00:00:00Z" }], next_cursor: null },
      },
      "/v1/publications/{publication_id}/results": {
        data: {
          id: "pub-1", campaign_id: "camp-1", snapshot_digest: "d", status: "published", supersedes_id: null,
          created_at: "2026-01-01T00:00:00Z", cohort_digest: "cohort-a",
          frozen_tasks: [{ slug: "t1", version: "0.1.0", family_id: "f", category: "rag" }],
          frozen_entrants: [{ slug: "observed", version: "1.0.0" }, { slug: "never-run", version: "1.0.0" }],
          snapshot: {
            schema_version: "aieb.analysis/v1", per_task: {}, per_entrant: { observed: 0.5 }, per_category: null,
            complete_for_rank: false, suite_rate: null, cost_per_resolution: null, total_campaign_cost_usd: null,
            verifier_cost_total_usd: null, successful_engineering_median_seconds: null, deadline_rate: null,
            infrastructure_attrition: null, per_entrant_valid_trials: { observed: 2 },
            per_entrant_resolved_tasks: { observed: 1 }, per_entrant_total_tasks: { observed: 1 },
            per_entrant_cost_per_resolution: {}, per_entrant_verifier_cost_usd: {},
            per_entrant_median_engineering_seconds: {}, per_entrant_deadline_rate: {},
            per_entrant_infrastructure_attrition: {}, limitations: [],
          },
        },
      },
    });
    renderWithProviders(<Results />);
    await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
    // The unobserved frozen entrant has a row (link in the row header).
    expect(screen.getByRole("link", { name: "never-run" })).toBeInTheDocument();
    const neverRunRow = screen.getByRole("link", { name: "never-run" }).closest("tr")!;
    // Its aggregate rate is Unknown (never a fabricated 0%); its valid-trials
    // count is a genuine 0 (frozen but unobserved).
    expect(within(neverRunRow).getAllByText("Unknown").length).toBeGreaterThan(0);
    expect(within(neverRunRow).getAllByText("0").length).toBeGreaterThan(0);
  });
});
