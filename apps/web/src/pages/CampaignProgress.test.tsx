import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CampaignProgress } from "./CampaignProgress";
import { api } from "../api/client";
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
    mockApi({
      "/v1/campaigns/{campaign_id}/progress": { data: progress },
      "/v1/campaigns/{campaign_id}/invalid-attempts": { data: [{ attempt_id: "attempt-2", trial_id: "trial-1", attempt_number: 2, terminal_status: "infrastructure_invalid" }] },
      "/v1/campaigns/{campaign_id}/invalid-attempts/{attempt_id}": { data: {
        campaign_id: "camp", trial_id: "trial-1", attempt_id: "attempt-2", attempt_number: 2,
        phase: "terminal", terminal_status: "infrastructure_invalid", created_at: "2026-09-17T00:00:00Z",
        events: [{ sequence: 1, event_type: "phase.started", created_at: "2026-09-17T00:00:00Z" }], reviews: [], can_review: false,
      } },
    });
    renderPage();
    fireEvent.click(await screen.findByRole("radio", { name: "Review attempt 2" }));
    expect(await screen.findByRole("region", { name: "Invalidity review evidence" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "trial-1" })).toHaveAttribute("href", "/runs/trial-1");
    expect(screen.getByText(/Reviewer or administrator role required/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Agree with classification/i })).not.toBeInTheDocument();
  });
  it("records an append-only review decision with a rationale when authorized", async () => {
    const user = userEvent.setup();
    const recorded: Array<{ decision: string; rationale: string }> = [];
    mockApi({
      "/v1/campaigns/{campaign_id}/progress": { data: progress },
      "/v1/campaigns/{campaign_id}/invalid-attempts": { data: [{ attempt_id: "attempt-2", trial_id: "trial-1", attempt_number: 2, terminal_status: "infrastructure_invalid" }] },
      "/v1/campaigns/{campaign_id}/invalid-attempts/{attempt_id}": () => ({
        data: {
          campaign_id: "camp", trial_id: "trial-1", attempt_id: "attempt-2", attempt_number: 2,
          phase: "terminal", terminal_status: "infrastructure_invalid", created_at: "2026-09-17T00:00:00Z",
          events: [],
          reviews: recorded.map((review, index) => ({
            id: `rev-${index + 1}`, attempt_id: "attempt-2", reviewer_id: "reviewer-u",
            decision: review.decision, rationale: review.rationale, created_at: "2026-09-17T00:00:01Z",
          })),
          can_review: true,
        },
      }),
    });
    const post = vi.spyOn(api, "POST").mockImplementation(((path: string, options?: { body?: { decision: string; rationale: string } }) => {
      expect(path).toBe("/v1/campaigns/{campaign_id}/invalid-attempts/{attempt_id}/reviews");
      if (options?.body) recorded.push(options.body);
      return Promise.resolve({
        data: { id: `rev-${recorded.length}`, attempt_id: "attempt-2", reviewer_id: "reviewer-u",
          decision: options?.body?.decision, rationale: options?.body?.rationale, created_at: "2026-09-17T00:00:01Z" },
        response: new Response(null, { status: 201 }),
      });
    }) as typeof api.POST);
    renderPage();
    fireEvent.click(await screen.findByRole("radio", { name: "Review attempt 2" }));
    await user.type(await screen.findByLabelText("Rationale"), "Confirmed outage");
    await user.click(screen.getByRole("button", { name: "Agree with classification" }));
    await waitFor(() => expect(recorded).toHaveLength(1));
    expect(recorded[0]).toEqual({ decision: "approve", rationale: "Confirmed outage" });
    expect(await screen.findByText("Confirmed outage", { selector: "td" })).toBeInTheDocument();
    expect(post).toHaveBeenCalledTimes(1);
  });
  it("reports partial query failures independently with request IDs", async () => {
    mockApi({ "/v1/campaigns/{campaign_id}/progress": { data: progress }, "/v1/campaigns/{campaign_id}/invalid-attempts": { status: 403, error: { error: { message: "Review access denied", request_id: "r-1" } } } });
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("Request ID: r-1");
    expect(screen.getByText("running")).toBeInTheDocument();
    expect(screen.queryByText("No invalid or cancelled attempts reported.")).not.toBeInTheDocument();
  });
});
