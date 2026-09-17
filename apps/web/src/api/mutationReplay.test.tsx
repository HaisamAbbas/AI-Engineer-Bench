import { act, renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "./client";
import { replayableWrite } from "./mutationReplay";
import { useCampaignWrite, type CampaignWrite } from "./hooks";
import { usePreparePublication, useReviewPublication, useWithdrawPublication, useRegradePublication } from "./publicationHooks";
import { useReviewInvalidAttempt } from "./invalidityHooks";

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{children}</QueryClientProvider>;
}
function useWrites() {
  const campaign = useCampaignWrite();
  const prepare = usePreparePublication("camp");
  const review = useReviewPublication();
  const withdraw = useWithdrawPublication();
  const regrade = useRegradePublication("camp");
  const invalidity = useReviewInvalidAttempt("camp", "attempt");
  return (action: string) => {
    if (action === "prepare") return prepare.mutateAsync({
      publication_class: "ranked", correction_run_id: null,
      correction_reason: null, supersedes_publication_id: null,
    });
    if (action === "review") return review.mutateAsync({ preparationId: "prep", body: { decision: "reject" } } as Parameters<typeof review.mutateAsync>[0]);
    if (action === "withdraw") return withdraw.mutateAsync({ publicationId: "pub", reason: "correction" });
    if (action === "regrade") return regrade.mutateAsync({} as Parameters<typeof regrade.mutateAsync>[0]);
    if (action === "invalidity") return invalidity.mutateAsync({ body: { decision: "reject", rationale: "review" } });
    return campaign.mutateAsync({ action, id: "camp", revision: 1, body: {} } as CampaignWrite);
  };
}
const principal = { authenticated: true as const, issuer: "test", subject: "operator", user_id: "user-1", roles: ["operator"] };
beforeEach(() => {
  sessionStorage.clear();
  vi.spyOn(api, "GET").mockResolvedValue({ data: principal, response: new Response() } as never);
});
afterEach(() => vi.restoreAllMocks());

describe("mutation replay", () => {
  it("keeps the key across token renewal for the same server principal", async () => {
    sessionStorage.setItem("aieb.oidc.access_token", "old-token");
    sessionStorage.setItem("aieb.oidc.expires_at", String(Date.now() + 60_000));
    const send = vi.fn<(headers: Record<string, string>) => Promise<string>>()
      .mockRejectedValueOnce(new TypeError("response lost"))
      .mockResolvedValue("committed");
    await expect(replayableWrite("create", { name: "test" }, send)).rejects.toThrow("response lost");
    sessionStorage.setItem("aieb.oidc.access_token", "renewed-token");
    await expect(replayableWrite("create", { name: "test" }, send)).resolves.toBe("committed");
    expect(send.mock.calls[1][0]["Idempotency-Key"]).toBe(send.mock.calls[0][0]["Idempotency-Key"]);
    expect(api.GET).toHaveBeenCalledWith("/v1/me");
  });

  it("does not reuse another principal's pending operation", async () => {
    const send = vi.fn<(headers: Record<string, string>) => Promise<string>>()
      .mockRejectedValueOnce(new TypeError("response lost"))
      .mockResolvedValue("committed");
    await expect(replayableWrite("create", {}, send)).rejects.toThrow("response lost");
    vi.mocked(api.GET).mockResolvedValue({ data: { ...principal, user_id: "user-2" }, response: new Response() } as never);
    await replayableWrite("create", {}, send);
    expect(send.mock.calls[1][0]["Idempotency-Key"]).not.toBe(send.mock.calls[0][0]["Idempotency-Key"]);
  });

  it("does not send a write when identity lookup fails", async () => {
    vi.mocked(api.GET).mockRejectedValue(new TypeError("identity unavailable"));
    const send = vi.fn();
    await expect(replayableWrite("create", {}, send)).rejects.toThrow("identity unavailable");
    expect(send).not.toHaveBeenCalled();
    expect(sessionStorage.length).toBe(0);
  });

  it("does not send a write for an unprovisioned identity", async () => {
    vi.mocked(api.GET).mockResolvedValue({ data: { ...principal, user_id: null }, response: new Response() } as never);
    const send = vi.fn();
    await expect(replayableWrite("create", {}, send)).rejects.toThrow("provisioned identity");
    expect(send).not.toHaveBeenCalled();
  });

  it("fails before sending if the replay handle cannot be persisted", async () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("storage unavailable"); });
    const send = vi.fn();
    await expect(replayableWrite("create", {}, send)).rejects.toThrow("storage unavailable");
    expect(send).not.toHaveBeenCalled();
  });

  it.each(["create", "patch", "freeze", "start", "pause", "resume", "cancel", "prepare", "review", "withdraw", "regrade", "invalidity"])(
    "retains %s identity after transport loss and remount, clears only after success", async action => {
      const method = action === "patch" ? "PATCH" : "POST";
      const post = vi.spyOn(api, method).mockRejectedValueOnce(new TypeError("response lost"))
        .mockResolvedValue({ data: { campaign: { id: "camp" } }, response: new Response() } as never);
      let hook = renderHook(useWrites, { wrapper });
      await act(async () => { await expect(hook.result.current(action)).rejects.toThrow("response lost"); });
      const first = (post.mock.calls[0]?.[1] as { headers: Record<string, string> }).headers?.["Idempotency-Key"];
      expect(first).toMatch(/^[0-9a-f-]{36}$/);
      hook.unmount();
      hook = renderHook(useWrites, { wrapper });
      await act(async () => { await hook.result.current(action); });
      expect((post.mock.calls[1]?.[1] as { headers: Record<string, string> }).headers["Idempotency-Key"]).toBe(first);
      await act(async () => { await hook.result.current(action); });
      expect((post.mock.calls[2]?.[1] as { headers: Record<string, string> }).headers["Idempotency-Key"]).not.toBe(first);
      hook.unmount();
    },
  );
});
