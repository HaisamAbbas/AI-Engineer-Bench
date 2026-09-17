import { describe, expect, it } from "vitest";
import { fireEvent, screen, within } from "@testing-library/react";
import { CampaignProgress } from "./CampaignProgress";
import { renderWithProviders } from "../test/renderWithProviders";
import { mockApi } from "../test/mockApi";
const progress = { campaign_id: "camp", state: "running", planned_trials: 9, observed_trials: 5, attempts_by_phase: [{ state: "engineering", count: 3 }], attempts_by_terminal_status: [{ state: "pass", count: 2 }], work_items_by_state: [{ state: "leased", count: 3 }] };
function renderPage() { return renderWithProviders(<CampaignProgress />, { route: "/admin/campaigns/camp/progress", path: "/admin/campaigns/:campaignId/progress" }); }

describe("CampaignProgress", () => {
  it("renders server counts without treating observed trials as completed and refreshes", async () => {
    let state = "running";
    mockApi({ "/v1/campaigns/{campaign_id}/progress": () => ({ data: { ...progress, state } }), "/v1/campaigns/{campaign_id}/invalid-attempts": { data: [] } });
    renderPage();
    expect(await screen.findByText("running")).toBeInTheDocument();
    expect(screen.getByText(/Observed trials are not completed/)).toBeInTheDocument();
    expect(within(screen.getByRole("table", { name: "Attempts by phase" })).getByText("3")).toBeInTheDocument();
    expect(screen.getByText("No invalid or cancelled attempts reported.")).toBeInTheDocument();
    state = "completed";
    fireEvent.click(screen.getByRole("button", { name: "Refresh progress" }));
    expect(await screen.findByText("completed")).toBeInTheDocument();
  });
  it("links invalid attempt evidence without pretending to record a review", async () => {
    mockApi({ "/v1/campaigns/{campaign_id}/progress": { data: progress }, "/v1/campaigns/{campaign_id}/invalid-attempts": { data: [{ attempt_id: "attempt-2", trial_id: "trial-1", attempt_number: 2, terminal_status: "infrastructure_invalid" }] } });
    renderPage();
    expect(await screen.findByText("attempt-2")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "trial-1" })).toHaveAttribute("href", "/runs/trial-1");
    expect(screen.getByText(/no review-decision write endpoint/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /approve/i })).not.toBeInTheDocument();
  });
  it("reports partial query failures independently with request IDs", async () => {
    mockApi({ "/v1/campaigns/{campaign_id}/progress": { data: progress }, "/v1/campaigns/{campaign_id}/invalid-attempts": { status: 403, error: { error: { message: "Review access denied", request_id: "r-1" } } } });
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("Request ID: r-1");
    expect(screen.getByText("running")).toBeInTheDocument();
    expect(screen.queryByText("No invalid or cancelled attempts reported.")).not.toBeInTheDocument();
  });
});
