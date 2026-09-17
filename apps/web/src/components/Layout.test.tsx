import { describe, expect, it, vi, afterEach } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { Layout } from "./Layout";
import { mockApi } from "../test/mockApi";
import { renderWithProviders } from "../test/renderWithProviders";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  sessionStorage.clear();
});

describe("Layout authentication wiring", () => {
  it("offers browser sign-in only when OIDC is configured", () => {
    vi.stubEnv("VITE_OIDC_ISSUER", "https://provider.example");
    vi.stubEnv("VITE_OIDC_CLIENT_ID", "web-client");
    renderWithProviders(<Layout />, { route: "/", path: "/" });
    expect(screen.getByRole("button", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Sign out" })).not.toBeInTheDocument();
  });

  it("stays honest when sign-in is not configured", () => {
    renderWithProviders(<Layout />, { route: "/", path: "/" });
    expect(screen.queryByRole("button", { name: "Sign in" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Sign out" })).not.toBeInTheDocument();
  });

  it("offers sign-in again when the token has expired", () => {
    vi.stubEnv("VITE_OIDC_ISSUER", "https://provider.example");
    vi.stubEnv("VITE_OIDC_CLIENT_ID", "web-client");
    sessionStorage.setItem("aieb.oidc.access_token", "expired-token");
    sessionStorage.setItem("aieb.oidc.expires_at", String(Date.now() - 1));
    renderWithProviders(<Layout />);
    expect(screen.getByRole("button", { name: "Sign in" })).toBeInTheDocument();
    expect(sessionStorage.getItem("aieb.oidc.access_token")).toBeNull();
  });

  it("renders sign-in discovery failures", async () => {
    vi.stubEnv("VITE_OIDC_ISSUER", "https://provider.example");
    vi.stubEnv("VITE_OIDC_CLIENT_ID", "web-client");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 503 })));
    renderWithProviders(<Layout />);
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("sign-in discovery failed");
    expect(screen.getByRole("button", { name: "Sign in" })).toBeEnabled();
  });

  it.each([{ roles: [] }, { roles: ["operator", "reviewer"] }])("shows server-resolved roles $roles", async ({ roles }) => {
    vi.stubEnv("VITE_OIDC_ISSUER", "https://provider.example");
    vi.stubEnv("VITE_OIDC_CLIENT_ID", "web-client");
    sessionStorage.setItem("aieb.oidc.access_token", "jwt-token");
    sessionStorage.setItem("aieb.oidc.expires_at", String(Date.now() + 300_000));
    mockApi({ "/v1/me": { data: { authenticated: true, subject: "server-user", issuer: "https://provider.example", user_id: null, roles } } });
    renderWithProviders(<Layout />);
    expect(await screen.findByText(/Signed in as server-user/)).toHaveTextContent(
      roles.length ? `Roles: ${roles.join(", ")}` : "No roles assigned",
    );
  });

  it("offers sign-out once a session token exists", () => {
    vi.stubEnv("VITE_OIDC_ISSUER", "https://provider.example");
    vi.stubEnv("VITE_OIDC_CLIENT_ID", "web-client");
    sessionStorage.setItem("aieb.oidc.access_token", "jwt-token");
    sessionStorage.setItem("aieb.oidc.expires_at", String(Date.now() + 300_000));
    renderWithProviders(<Layout />, { route: "/", path: "/" });
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });
});
