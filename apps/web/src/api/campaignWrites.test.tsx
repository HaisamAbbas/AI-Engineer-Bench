import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./client";
import { useCampaignWrite, type CampaignWrite } from "./hooks";
import { renderWithProviders } from "../test/renderWithProviders";

function WriteButton({ input }: { input: CampaignWrite }) {
  const write = useCampaignWrite();
  return <><button onClick={() => write.mutate(structuredClone(input))} disabled={write.isPending}>Write</button>
    {write.isError && <p role="alert">Request failed</p>}
    {write.isSuccess && <p>Saved</p>}</>;
}

afterEach(() => vi.restoreAllMocks());

describe("campaign mutation identity", () => {
  it.each(["create", "freeze", "start"] as const)("sends a key and preserves it after a lost %s response", async action => {
    const post = vi.spyOn(api, "POST")
      .mockRejectedValueOnce(new TypeError("connection lost"))
      .mockResolvedValue({ data: { campaign: { id: "camp", state: "running" } }, response: new Response() } as never);
    const input = action === "create" ? { action, body: {} } : { action, id: "camp", body: {} };
    renderWithProviders(<WriteButton input={input as CampaignWrite} />);
    fireEvent.click(screen.getByRole("button", { name: "Write" }));
    await screen.findByRole("alert");
    fireEvent.click(screen.getByRole("button", { name: "Write" }));
    await screen.findByText("Saved");
    const first = post.mock.calls[0]?.[1] as { headers?: Record<string, string> } | undefined;
    const second = post.mock.calls[1]?.[1] as { headers?: Record<string, string> } | undefined;
    expect(first?.headers?.["Idempotency-Key"]).toMatch(/^[0-9a-f-]{36}$/);
    expect(second?.headers?.["Idempotency-Key"]).toBe(first?.headers?.["Idempotency-Key"]);
    fireEvent.click(screen.getByRole("button", { name: "Write" }));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(3));
    const third = post.mock.calls[2]?.[1] as { headers?: Record<string, string> } | undefined;
    expect(third?.headers?.["Idempotency-Key"]).not.toBe(first?.headers?.["Idempotency-Key"]);
  });
});
