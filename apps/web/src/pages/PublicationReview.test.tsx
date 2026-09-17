import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, renderHook, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { PublicationReview } from "./PublicationReview";
import { usePublicationCorrectionRun, useWithdrawPublication } from "../api/publicationHooks";
import { api } from "../api/client";
import { renderWithProviders } from "../test/renderWithProviders";
import { mockAuthenticatedApi as mockApi } from "../test/mockApi";

beforeEach(() => { sessionStorage.clear(); mockApi(); });

const preparePath = "/v1/campaigns/{campaign_id}/publications/prepare";
const reviewPath = "/v1/publications/preparations/{preparation_id}/review";
const regradePath = "/v1/campaigns/{campaign_id}/regrade";
const withdrawPath = "/v1/publications/{publication_id}/withdraw";
const exportPath = "/v1/publications/{publication_id}/export";
const runPath = "/v1/correction-runs/{run_id}";
const prepared = {
  id: "prep-1", campaign_id: "camp-1", status: "prepared", snapshot_digest: "sha256:snapshot",
  evidence_manifest_digest: "sha256:evidence", review_kind: null, supersedes_publication_id: null,
  published_publication_id: null, created_at: "2026-09-17T00:00:00Z", publication_class: "ranked",
};
const exported = {
  publication_id: "pub-1", campaign_id: "camp-1", status: "published", snapshot_digest: "sha256:export",
  snapshot: {}, cohort: null, frozen_tasks: [], frozen_entrants: [], signature: null, runs: [], notice: "Fixture export",
};
const running = { id: "run-1", campaign_id: "camp-1", status: "running", regrade_work_items: 3, created_at: "2026-09-17T00:00:00Z" };

type MockResponse = { data?: unknown; error?: unknown; status?: number };
function mockPost(routes: Record<string, MockResponse>) {
  return vi.spyOn(api, "POST").mockImplementation(((path: string) => {
    const result = routes[path];
    if (!result) throw new Error(`Unexpected POST ${path}`);
    return Promise.resolve({ ...result, response: new Response(null, { status: result.status ?? 200 }) });
  }) as typeof api.POST);
}
function renderPage() {
  return renderWithProviders(<PublicationReview />, {
    route: "/admin/campaigns/camp-1/publications", path: "/admin/campaigns/:campaignId/publications",
  });
}
function fill(label: string | RegExp, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

describe("PublicationReview", () => {
  it("discloses API and authorization limits and does not invent preparation reads", () => {
    const get = mockApi({});
    renderPage();
    expect(screen.getByText(/Real public publication is not authorized/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Load preparation evidence" })).toBeDisabled();
    expect(screen.getByText(/server-authenticated identity/)).toBeInTheDocument();
    expect(screen.getByText(/frozen registry is not exposed/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reject preparation" })).toBeDisabled();
    expect(get).not.toHaveBeenCalled();
  });

  it("shows preparation state and digests, blocks local approval, and allows rejection", async () => {
    const user = userEvent.setup();
    mockApi({});
    const post = mockPost({ [preparePath]: { data: prepared }, [reviewPath]: { data: { ...prepared, status: "rejected" } } });
    renderPage();
    await user.click(screen.getByRole("button", { name: "Prepare publication" }));
    expect(await screen.findByText("sha256:snapshot")).toBeInTheDocument();
    expect(screen.getByText("sha256:evidence")).toBeInTheDocument();
    expect(post).toHaveBeenCalledWith(preparePath, { params: { path: { campaign_id: "camp-1" } },
      headers: { "Idempotency-Key": expect.any(String) },
      body: { supersedes_publication_id: null, correction_run_id: null, correction_reason: null, publication_class: "ranked" } });
    await user.selectOptions(screen.getByLabelText("Decision"), "approve");
    await user.click(screen.getByRole("checkbox", { name: /I confirm this is a staging fixture/ }));
    expect(screen.getByRole("button", { name: "Approve and publish staging fixture" })).toBeDisabled();
    fill("Preparation ID", " PREP-1 ");
    await user.click(screen.getByRole("checkbox", { name: /I confirm this is a staging fixture/ }));
    expect(screen.getByRole("button", { name: "Approve and publish staging fixture" })).toBeDisabled();
    await user.selectOptions(screen.getByLabelText("Decision"), "reject");
    await user.click(screen.getByRole("button", { name: "Reject preparation" }));
    expect(await screen.findByText("rejected", { selector: "dd" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reject preparation" })).toBeDisabled();
    expect(post).toHaveBeenCalledTimes(2);
  });

  it("requires fixture acknowledgement for manual approval and records the independence attestation", async () => {
    const user = userEvent.setup();
    mockApi({});
    const post = mockPost({ [reviewPath]: { data: { ...prepared, status: "published", published_publication_id: "pub-1", review_kind: "independent" } } });
    renderPage();
    fill("Preparation ID", "prep-other");
    await user.selectOptions(screen.getByLabelText("Decision"), "approve");
    const approve = screen.getByRole("button", { name: "Approve and publish staging fixture" });
    expect(approve).toBeDisabled();
    await user.click(screen.getByRole("checkbox", { name: /independently of the preparation/i }));
    fill("Preparation ID", "prep-1");
    expect(approve).toBeDisabled();
    await user.click(screen.getByRole("checkbox", { name: "I confirm this is a staging fixture, not a real public publication." }));
    fill("Review notes (optional)", "Evidence reviewed");
    await user.click(approve);
    expect(await screen.findByRole("link", { name: "pub-1" })).toHaveAttribute("href", "/releases/pub-1");
    expect(post).toHaveBeenCalledWith(reviewPath, { params: { path: { preparation_id: "prep-1" } },
      headers: { "Idempotency-Key": expect.any(String) },
      body: { decision: "approve", independence_attestation: true, notes: "Evidence reviewed" } });
    expect(approve).toBeDisabled();
  });

  it("loads prepared materials without publishing and reports server identity restrictions", async () => {
    const get = mockApi({ "/v1/publications/preparations/{preparation_id}": { data: {
      preparation: prepared, snapshot: { coverage: "fixture coverage" },
      evidence_manifest: { selections: [{ trial_id: "trial-pinned", included: true, evaluation_id: "eval-pinned" }] },
      can_approve: false, approval_blocked_reason: "Campaign creator cannot approve", correction_reason: null,
    } } });
    const post = mockPost({});
    renderPage();
    fill("Preparation ID to inspect", "prep-1");
    fireEvent.click(screen.getByRole("button", { name: "Load preparation evidence" }));
    expect(await screen.findByText("Campaign creator cannot approve")).toBeInTheDocument();
    expect(screen.getByText(/fixture coverage/)).toBeInTheDocument();
    expect(screen.getByText(/eval-pinned/)).toBeInTheDocument();
    expect(get).toHaveBeenCalledWith("/v1/publications/preparations/{preparation_id}", {
      params: { path: { preparation_id: "prep-1" } },
    });
    expect(post).not.toHaveBeenCalled();
  });

  it("requires correction information before a superseding preparation", async () => {
    const user = userEvent.setup();
    mockApi({});
    const post = mockPost({ [preparePath]: { data: { ...prepared, supersedes_publication_id: "pub-old" } } });
    renderPage();
    fill("Supersedes publication ID (optional)", "pub-old");
    const submit = screen.getByRole("button", { name: "Prepare publication" });
    expect(submit).toBeDisabled();
    fill("Completed correction run ID (optional)", "run-1");
    fill("Correction reason", "Corrected scoring");
    await user.click(submit);
    await waitFor(() => expect(post).toHaveBeenCalledWith(preparePath, { params: { path: { campaign_id: "camp-1" } },
      headers: { "Idempotency-Key": expect.any(String) },
      body: { supersedes_publication_id: "pub-old", correction_run_id: "run-1", correction_reason: "Corrected scoring", publication_class: "ranked" } }));
  });

  it("surfaces server denial with request ID and never resubmits a mutation via error Retry", async () => {
    const user = userEvent.setup();
    mockApi({});
    const post = mockPost({ [reviewPath]: { status: 403, error: { error: {
      code: "forbidden", message: "preparer and campaign creator cannot approve their own publication", request_id: "req-denied",
    } } } });
    renderPage();
    fill("Preparation ID", "manual-id");
    await user.selectOptions(screen.getByLabelText("Decision"), "approve");
    await user.click(screen.getByRole("checkbox", { name: /I confirm this is a staging fixture/ }));
    await user.click(screen.getByRole("button", { name: "Approve and publish staging fixture" }));
    expect(await screen.findByText("Request ID: req-denied")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(post).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("link", { name: "pub-1" })).not.toBeInTheDocument();
  });

  it("loads installed scoring guidance, validates JSON and starts regrading without publishing", async () => {
    const user = userEvent.setup();
    const get = mockApi({
      "/v1/campaigns/{campaign_id}/scoring-bundle": { data: { scoring_digest: "sha256:installed", scope: "installed-staging-only" } },
      [runPath]: { data: { ...running, status: "completed" } },
    });
    const post = mockPost({ [regradePath]: { data: running } });
    renderPage();
    await user.click(screen.getByRole("button", { name: "Get scoring bundle" }));
    expect(await screen.findByText("sha256:installed")).toBeInTheDocument();
    fill("Registry JSON", "not json");
    fill("Regrade reason", "fixture correction");
    await user.click(screen.getByRole("button", { name: "Start staging regrade" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Registry must be valid JSON");
    fill("Registry JSON", "[]");
    await user.click(screen.getByRole("button", { name: "Start staging regrade" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Registry must contain");
    expect(post).not.toHaveBeenCalled();
    const registry = { cohort: {}, protocol: { scoring_digest: "sha256:installed" }, budget: {} };
    fill("Registry JSON", JSON.stringify(registry));
    await user.click(screen.getByRole("button", { name: "Start staging regrade" }));
    expect(await screen.findByText(/Correction completed/)).toBeInTheDocument();
    expect(post).toHaveBeenCalledWith(regradePath, { params: { path: { campaign_id: "camp-1" } },
      headers: { "Idempotency-Key": expect.any(String) }, body: { registry, reason: "fixture correction" } });
    expect(post).toHaveBeenCalledTimes(1);
    expect(get).toHaveBeenCalledWith(runPath, { params: { path: { run_id: "run-1" } } });
  });

  it("withdraws with a required reason and refreshes a previously loaded public export", async () => {
    const user = userEvent.setup();
    let status = "published";
    mockApi({ [exportPath]: () => ({ data: { ...exported, status } }) });
    const post = mockPost({ [withdrawPath]: { data: { ...exported, status: "withdrawn" } } });
    renderPage();
    fill("Publication ID to export", "pub-1");
    await user.click(screen.getByRole("button", { name: "Load public export" }));
    expect(await screen.findByText("published", { selector: "dd" })).toBeInTheDocument();
    expect(screen.getByText("Fixture export")).toBeInTheDocument();
    fill("Publication ID to withdraw", "pub-1");
    expect(screen.getByRole("button", { name: "Withdraw publication" })).toBeDisabled();
    fill("Withdrawal reason", "  fixture only  ");
    status = "withdrawn";
    await user.click(screen.getByRole("button", { name: "Withdraw publication" }));
    expect(await screen.findByText("withdrawn", { selector: "dd" })).toBeInTheDocument();
    expect(post).toHaveBeenCalledWith(withdrawPath, { params: { path: { publication_id: "pub-1" } },
      headers: { "Idempotency-Key": expect.any(String) }, body: { reason: "fixture only" } });
    expect(screen.getByText(/not cryptographic verification/)).toBeInTheDocument();
  });

  it("shows a manual correction-run failure and a retryable export error", async () => {
    const user = userEvent.setup();
    mockApi({
      [runPath]: { data: { ...running, status: "failed" } },
      [exportPath]: { status: 404, error: { error: { message: "Publication not found", request_id: "req-export", code: "not_found" } } },
    });
    renderPage();
    fill("Correction run ID", "run-1");
    await user.click(screen.getByRole("button", { name: "Check correction run" }));
    expect(await screen.findByText(/Correction failed/)).toBeInTheDocument();
    fill("Publication ID to export", "missing");
    await user.click(screen.getByRole("button", { name: "Load public export" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Publication not found");
    expect(screen.getByText("Request ID: req-export")).toBeInTheDocument();
  });

  it("shows loading during preparation and prevents duplicate submission", async () => {
    const user = userEvent.setup();
    mockApi({});
    let resolve!: (value: unknown) => void;
    const pending = new Promise<unknown>((done) => { resolve = done; });
    const post = vi.spyOn(api, "POST").mockImplementation((() => pending) as typeof api.POST);
    renderPage();
    await user.click(screen.getByRole("button", { name: "Prepare publication" }));
    expect(await screen.findByText("Loading preparation…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Prepare publication" })).toBeDisabled();
    await act(async () => resolve({ data: prepared, response: new Response(null, { status: 200 }) }));
    expect(await screen.findByText("sha256:snapshot")).toBeInTheDocument();
    expect(post).toHaveBeenCalledTimes(1);
  });
});

describe("publication hooks", () => {
  function providers(client: QueryClient) {
    return function Wrapper({ children }: { children: ReactNode }) {
      return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
    };
  }

  it.each(["completed", "failed"])("polls running corrections and stops at %s", async (terminal) => {
    vi.useFakeTimers();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    let count = 0;
    const get = mockApi({ [runPath]: () => ({ data: { ...running, status: ++count === 1 ? "running" : terminal } }) });
    const hook = renderHook(() => usePublicationCorrectionRun("run-1"), { wrapper: providers(client) });
    try {
      await act(async () => { await vi.advanceTimersByTimeAsync(20); });
      expect(hook.result.current.data?.status).toBe("running");
      await act(async () => { await vi.advanceTimersByTimeAsync(2100); });
      expect(hook.result.current.data?.status).toBe(terminal);
      const calls = get.mock.calls.length;
      await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
      expect(get).toHaveBeenCalledTimes(calls);
    } finally {
      hook.unmount();
      client.clear();
      vi.useRealTimers();
    }
  });

  it("invalidates releases, corrections, and publication caches after a write", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const keys = [["releases", null], ["corrections", null], ["publication-results", "pub-1"], ["publication-export", "pub-1"]];
    keys.forEach((key) => client.setQueryData(key, { fixture: true }));
    client.setQueryData(["tasks"], { fixture: true });
    mockPost({ [withdrawPath]: { data: { ...exported, status: "withdrawn" } } });
    const hook = renderHook(() => useWithdrawPublication(), { wrapper: providers(client) });
    await act(async () => { await hook.result.current.mutateAsync({ publicationId: "pub-1", reason: "fixture" }); });
    keys.forEach((key) => expect(client.getQueryState(key)?.isInvalidated).toBe(true));
    expect(client.getQueryState(["tasks"])?.isInvalidated).toBe(false);
    hook.unmount();
    client.clear();
  });
});
