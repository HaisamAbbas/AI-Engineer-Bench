import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { StrictMode } from "react";
import { api } from "../api/client";

const metadata = () => new Response(JSON.stringify({ issuer: "https://provider.example", authorization_endpoint: "https://provider.example/oauth/authorize", token_endpoint: "https://provider.example/oauth/token" }));
beforeEach(() => {
  sessionStorage.setItem("aieb.oidc.state", "expected-state");
  sessionStorage.setItem("aieb.oidc.started", String(Date.now()));
});
import { screen, waitFor } from "@testing-library/react";
import { AuthCallback } from "./AuthCallback";
import { renderWithProviders } from "../test/renderWithProviders";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  sessionStorage.clear();
});

describe("AuthCallback", () => {
  it("exchanges the authorization code with the stored PKCE verifier", async () => {
    vi.stubEnv("VITE_OIDC_ISSUER", "https://provider.example");
    vi.stubEnv("VITE_OIDC_CLIENT_ID", "web-client");
    sessionStorage.setItem("aieb.oidc.pkce", "verifier-123");
    const fetchMock = vi.fn().mockResolvedValueOnce(metadata()).mockResolvedValueOnce(
      new Response(JSON.stringify({ access_token: "jwt-token", token_type: "Bearer", expires_in: 300 }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<StrictMode><AuthCallback /></StrictMode>, { route: "/auth/callback?code=one-time-code&state=expected-state", path: "/auth/callback" });
    await waitFor(() => expect(sessionStorage.getItem("aieb.oidc.access_token")).toBe("jwt-token"));
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock).toHaveBeenCalledWith("https://provider.example/oauth/token", expect.objectContaining({ method: "POST" }));
    const body = String(fetchMock.mock.calls[1][1].body);
    expect(body).toContain("grant_type=authorization_code");
    expect(body).toContain("code=one-time-code");
    expect(body).toContain("code_verifier=verifier-123");
    expect(body).toContain("client_id=web-client");
    expect(sessionStorage.getItem("aieb.oidc.access_token")).toBe("jwt-token");
    expect(sessionStorage.getItem("aieb.oidc.pkce")).toBeNull();
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ campaign: {} }), { headers: { "Content-Type": "application/json" } }));
    await api.GET("/v1/campaigns/{campaign_id}", { params: { path: { campaign_id: "camp" } }, fetch: fetchMock });
    expect((fetchMock.mock.calls[2][0] as Request).headers.get("Authorization")).toBe("Bearer jwt-token");
  });

  it("surfaces an error response instead of pretending sign-in succeeded", async () => {
    vi.stubEnv("VITE_OIDC_ISSUER", "https://provider.example");
    vi.stubEnv("VITE_OIDC_CLIENT_ID", "web-client");
    const fetchMock = vi.fn().mockResolvedValueOnce(metadata()).mockResolvedValueOnce(new Response("denied", { status: 400 }));
    vi.stubGlobal("fetch", fetchMock);
    sessionStorage.setItem("aieb.oidc.pkce", "verifier-123");
    renderWithProviders(<AuthCallback />, { route: "/auth/callback?code=one-time-code&state=expected-state", path: "/auth/callback" });
    expect(await screen.findByRole("alert")).toHaveTextContent("token exchange failed");
    expect(sessionStorage.getItem("aieb.oidc.access_token")).toBeNull();
  });

  it.each(["wrong-state", ""])("rejects invalid callback state %s without contacting the provider", async state => {
    vi.stubEnv("VITE_OIDC_ISSUER", "https://provider.example");
    vi.stubEnv("VITE_OIDC_CLIENT_ID", "web-client");
    sessionStorage.setItem("aieb.oidc.pkce", "verifier-123");
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<AuthCallback />, { route: `/auth/callback?code=code&state=${state}`, path: "/auth/callback" });
    expect(await screen.findByRole("alert")).toHaveTextContent("invalid or expired sign-in state");
    expect(fetchMock).not.toHaveBeenCalled();
    expect(sessionStorage.getItem("aieb.oidc.access_token")).toBeNull();
  });

  it("does not fake success when the provider returned an error redirect", async () => {
    vi.stubEnv("VITE_OIDC_ISSUER", "https://provider.example");
    vi.stubEnv("VITE_OIDC_CLIENT_ID", "web-client");
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<AuthCallback />, { route: "/auth/callback?error=access_denied", path: "/auth/callback" });
    expect(await screen.findByRole("alert")).toHaveTextContent("access_denied");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
