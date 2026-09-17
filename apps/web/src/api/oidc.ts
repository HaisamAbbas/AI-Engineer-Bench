// Minimal browser OIDC (authorization code + PKCE) for the maintainer UI.

// The SPA only OBTAINS and PRESENTS a token: the API verifies it
// cryptographically and resolves every role server-side (auth.py), so the
// browser never decides what an identity may do. Without deployment config
// (VITE_OIDC_ISSUER + VITE_OIDC_CLIENT_ID) no sign-in exists at all - there is
// no demo bypass, mirroring the API's fail-closed provider rule.

export interface OidcConfig {
  issuer: string;
  clientId: string;
}

export function configuredOidc(): OidcConfig | undefined {
  const issuer = import.meta.env.VITE_OIDC_ISSUER as string | undefined;
  const clientId = import.meta.env.VITE_OIDC_CLIENT_ID as string | undefined;
  return issuer && clientId ? { issuer, clientId } : undefined;
}

const PKCE_KEY = "aieb.oidc.pkce";
const TOKEN_KEY = "aieb.oidc.access_token";
const EXPIRY_KEY = "aieb.oidc.expires_at";
const RETURN_KEY = "aieb.oidc.return_to";
const STATE_KEY = "aieb.oidc.state";
const STARTED_KEY = "aieb.oidc.started";

async function discovery(config: OidcConfig) {
  const issuer = new URL(config.issuer);
  if (issuer.protocol !== "https:") throw new Error("sign-in requires an HTTPS issuer");
  const response = await fetch(`${config.issuer.replace(/\/$/, "")}/.well-known/openid-configuration`);
  if (!response.ok) throw new Error("sign-in discovery failed");
  const metadata = await response.json() as Record<string, unknown>;
  if (metadata.issuer !== config.issuer) throw new Error("sign-in issuer mismatch");
  for (const key of ["authorization_endpoint", "token_endpoint"]) {
    if (typeof metadata[key] !== "string" || new URL(metadata[key]).protocol !== "https:") {
      throw new Error("sign-in requires HTTPS provider endpoints");
    }
  }
  return metadata as { authorization_endpoint: string; token_endpoint: string };
}

export function accessToken(): string | undefined {
  const expires = Number(sessionStorage.getItem(EXPIRY_KEY));
  if (!Number.isFinite(expires) || expires <= Date.now()) {
    sessionStorage.removeItem(TOKEN_KEY);
    sessionStorage.removeItem(EXPIRY_KEY);
    return undefined;
  }
  return sessionStorage.getItem(TOKEN_KEY) ?? undefined;
}

export function clearSession(): void {
  for (const key of [TOKEN_KEY, EXPIRY_KEY, PKCE_KEY, STATE_KEY, STARTED_KEY, RETURN_KEY]) sessionStorage.removeItem(key);
}

export function consumeReturnTo(): string {
  const target = sessionStorage.getItem(RETURN_KEY) ?? "/";
  sessionStorage.removeItem(RETURN_KEY);
  return target.startsWith("/") && !target.startsWith("//") && !target.includes("\\") ? target : "/";
}

function base64Url(bytes: Uint8Array): string {
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function pkcePair(): Promise<{ verifier: string; challenge: string }> {
  const verifier = base64Url(crypto.getRandomValues(new Uint8Array(32)));
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return { verifier, challenge: base64Url(new Uint8Array(digest)) };
}

/** Redirect to the provider's authorization endpoint with PKCE. The code
 * verifier is kept in sessionStorage and exchanged in the callback only. */
export async function beginSignIn(returnTo: string): Promise<void> {
  const config = configuredOidc();
  if (!config) throw new Error("sign-in is not configured for this deployment");
  const metadata = await discovery(config);
  const redirectUri = `${window.location.origin}/auth/callback`;
  const { verifier, challenge } = await pkcePair();
  const state = base64Url(crypto.getRandomValues(new Uint8Array(32)));
  sessionStorage.setItem(PKCE_KEY, verifier);
  sessionStorage.setItem(STATE_KEY, state);
  sessionStorage.setItem(STARTED_KEY, String(Date.now()));
  sessionStorage.setItem(RETURN_KEY, returnTo);
  const authorize = new URL(metadata.authorization_endpoint);
  authorize.searchParams.set("state", state);
  const audience = import.meta.env.VITE_OIDC_AUDIENCE as string | undefined;
  if (audience) authorize.searchParams.set("audience", audience);
  authorize.searchParams.set("response_type", "code");
  authorize.searchParams.set("client_id", config.clientId);
  authorize.searchParams.set("redirect_uri", redirectUri);
  authorize.searchParams.set("scope", "openid");
  authorize.searchParams.set("code_challenge", challenge);
  authorize.searchParams.set("code_challenge_method", "S256");
  window.location.assign(authorize);
}

/** Exchange the authorization code for tokens. Fails loudly on any provider
 * error; success stores the access token and consumes the PKCE verifier. */
export async function exchangeCode(code: string, state: string | null): Promise<void> {
  const config = configuredOidc();
  const verifier = sessionStorage.getItem(PKCE_KEY);
  const started = Number(sessionStorage.getItem(STARTED_KEY));
  if (!state || state !== sessionStorage.getItem(STATE_KEY) || !started ||
      Date.now() - started > 600_000 || started > Date.now()) {
    throw new Error("token exchange failed: invalid or expired sign-in state");
  }
  if (!config || !verifier) throw new Error("token exchange failed: no pending sign-in request");
  // Consume before any asynchronous work: an authorization code is single-use.
  for (const key of [PKCE_KEY, STATE_KEY, STARTED_KEY, TOKEN_KEY]) sessionStorage.removeItem(key);
  const metadata = await discovery(config);
  const body = new URLSearchParams({
    grant_type: "authorization_code",
    code,
    code_verifier: verifier,
    client_id: config.clientId,
    redirect_uri: `${window.location.origin}/auth/callback`,
  });
  const response = await fetch(metadata.token_endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
  if (!response.ok) throw new Error(`token exchange failed with HTTP ${response.status}`);
  const payload = (await response.json()) as { access_token?: unknown; token_type?: unknown; expires_in?: unknown };
  if (typeof payload.access_token !== "string" || !payload.access_token ||
      typeof payload.token_type !== "string" || payload.token_type.toLowerCase() !== "bearer" ||
      typeof payload.expires_in !== "number" || !Number.isFinite(payload.expires_in) || payload.expires_in <= 0) {
    throw new Error("token exchange failed: invalid bearer token response");
  }
  sessionStorage.setItem(EXPIRY_KEY, String(Date.now() + payload.expires_in * 1000));
  sessionStorage.setItem(TOKEN_KEY, payload.access_token);
}
