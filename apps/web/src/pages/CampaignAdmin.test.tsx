import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { CampaignAdmin } from "./CampaignAdmin";
import { api } from "../api/client";
import { renderWithProviders } from "../test/renderWithProviders";
import { mockAuthenticatedApi as mockApi } from "../test/mockApi";
const summary = (state: string) => ({ id: "camp", name: "Fixture campaign", state, revision: 4, manifest_digest: "digest", cohort_digest: "cohort" });
const result = (data: unknown) => ({ data, response: new Response(null, { status: 200 }) });
function renderDetail() { return renderWithProviders(<CampaignAdmin />, { route: "/admin/campaigns/camp", path: "/admin/campaigns/:campaignId" }); }

describe("CampaignAdmin", () => {
  it("does not invent a campaign list endpoint", () => {
    const get = mockApi({});
    renderWithProviders(<CampaignAdmin />);
    expect(screen.getByText(/Campaign listing is not available/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create campaign" })).toBeDisabled();
    expect(get).not.toHaveBeenCalled();
  });
  it("prefills the saved draft and saves edits with its revision", async () => {
    const draft = { id: "saved-draft", repetitions: 2 };
    mockApi({ "/v1/campaigns/{campaign_id}": { data: { campaign: summary("draft"), draft } } });
    const patch = vi.spyOn(api, "PATCH").mockResolvedValue(result(summary("draft")) as never);
    renderDetail();
    const editor = await screen.findByLabelText("Replacement draft JSON");
    expect(editor).toHaveValue(JSON.stringify(draft, null, 2));
    const edited = { ...draft, repetitions: 3 };
    fireEvent.change(editor, { target: { value: JSON.stringify(edited) } });
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
    await waitFor(() => expect(patch).toHaveBeenCalledWith("/v1/campaigns/{campaign_id}", {
      params: { path: { campaign_id: "camp" } }, body: { draft: edited }, headers: { "If-Match": "4", "Idempotency-Key": expect.any(String) },
    }));
  });
  it("never edits frozen plans and displays honest reservation enforcement", async () => {
    mockApi({ "/v1/campaigns/{campaign_id}": { data: { campaign: summary("frozen"), reservation: { reservation_id: "r", enforcement: "estimated_time_limited", reserved_usd: "12.50", status: "active" } } } });
    renderDetail();
    expect(await screen.findByText(/frozen plan is immutable/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Replacement draft JSON")).not.toBeInTheDocument();
    expect(screen.getByText(/not a hard provider cap/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start campaign" })).toBeEnabled();
  });
  it("requires cancellation acknowledgement and waits for authoritative state", async () => {
    let state = "running";
    mockApi({ "/v1/campaigns/{campaign_id}": () => ({ data: { campaign: summary(state) } }) });
    let finish!: (value: ReturnType<typeof result>) => void;
    const post = vi.spyOn(api, "POST").mockImplementation((() => new Promise<ReturnType<typeof result>>(resolve => { finish = resolve; })) as typeof api.POST);
    renderDetail();
    const cancel = await screen.findByRole("button", { name: "Confirm cancellation" });
    expect(cancel).toBeDisabled();
    expect(screen.getByText(/Leased work finishes or expires/)).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("I understand the cancellation consequences"));
    fireEvent.click(cancel);
    await waitFor(() => expect(post).toHaveBeenCalledWith("/v1/campaigns/{campaign_id}/cancel", { params: { path: { campaign_id: "camp" } }, headers: { "Idempotency-Key": expect.any(String) } }));
    expect(screen.getByText("running")).toBeInTheDocument();
    expect(cancel).toBeDisabled();
    state = "cancelling";
    finish(result({ campaign: summary(state), notice: "Cancelling" }));
    expect(await screen.findByText("cancelling")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Confirm cancellation" })).not.toBeInTheDocument();
  });
  it("sends the saved revision in If-Match and surfaces stale revision errors", async () => {
    mockApi({ "/v1/campaigns/{campaign_id}": { data: { campaign: summary("draft") } } });
    const patch = vi.spyOn(api, "PATCH").mockResolvedValue({ error: { error: { code: "stale_revision", message: "Stale revision", request_id: "req-4" } }, response: new Response(null, { status: 409 }) } as never);
    renderDetail();
    fireEvent.change(await screen.findByLabelText("Replacement draft JSON"), { target: { value: '{"id":"draft"}' } });
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
    expect(await screen.findByText("Stale revision")).toBeInTheDocument();
    expect(screen.getByText("Request ID: req-4")).toBeInTheDocument();
    expect(patch).toHaveBeenCalledWith("/v1/campaigns/{campaign_id}", { params: { path: { campaign_id: "camp" } }, body: { draft: { id: "draft" } }, headers: { "If-Match": "4", "Idempotency-Key": expect.any(String) } });
    expect(patch).toHaveBeenCalledTimes(1);
  });
  it("renders the server matrix and clears it when registry input changes", async () => {
    mockApi({ "/v1/campaigns/{campaign_id}": { data: { campaign: summary("draft") } } });
    const post = vi.spyOn(api, "POST").mockResolvedValue(result({ campaign_id: "camp", trial_count: 7, cohort_id: "c", protocol_id: "p", budget_profile_id: "b", reserved_budget_usd: null, cells: [{ task_id: "task-a", entrant_id: "entrant-a", repetitions: 7 }], trials: Array.from({ length: 7 }, (_, index) => ({ trial_id: `trial-${index}`, task_id: "task-a", entrant_id: "entrant-a", order_index: index, repetition_index: 6 - index })) }) as never);
    renderDetail();
    const registry = await screen.findByLabelText("Registry JSON");
    fireEvent.change(registry, { target: { value: '{}' } });
    fireEvent.click(screen.getByRole("button", { name: "Preview exact matrix" }));
    expect(await screen.findByText(/Server preview: 7 trials/)).toBeInTheDocument();
    expect(screen.getAllByText("task-a")).toHaveLength(7);
    expect(screen.getAllByRole("row")[1]).toHaveTextContent("0trial-0task-aentrant-a6");
    expect(screen.getAllByRole("row")[7]).toHaveTextContent("6trial-6task-aentrant-a0");
    expect(screen.getByRole("button", { name: "Freeze campaign" })).toBeDisabled();
    expect(post).toHaveBeenCalledWith("/v1/campaigns/{campaign_id}/preview", { params: { path: { campaign_id: "camp" } }, body: {} });
    fireEvent.change(registry, { target: { value: '{"cohort":{}}' } });
    expect(screen.queryByText("task-a")).not.toBeInTheDocument();
  });
  it("shows authorization failures without editable controls", async () => {
    mockApi({ "/v1/campaigns/{campaign_id}": { status: 403, error: { error: { message: "Operator access denied", request_id: "auth-1" } } } });
    renderDetail();
    expect(await screen.findByRole("alert")).toHaveTextContent("Operator access denied");
    expect(screen.queryByRole("button", { name: "Start campaign" })).not.toBeInTheDocument();
  });
});
