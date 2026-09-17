import { api, API_BASE_URL, unwrap } from "./client";

// Only hashes and random replay handles are persisted, not bodies or tokens.
// Storage failure aborts BEFORE sending; silently losing a handle is unsafe.
export async function replayableWrite<T>(scope: string, input: unknown, send: (headers: Record<string, string>) => Promise<T>): Promise<T> {
  // Token renewal must not turn an uncertain write into a new operation.
  // Use the API-verified stable principal, never decoded claims or token bytes.
  // If identity cannot be established, fail before sending the mutation.
  const principal = await unwrap(api.GET("/v1/me"));
  if (!principal.user_id) throw new Error("A provisioned identity is required for writes");
  const identity = JSON.stringify([API_BASE_URL, principal.issuer, principal.user_id, scope, input]);
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(identity));
  const fingerprint = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
  const storageKey = `aieb.replay.${fingerprint}`;
  let key = sessionStorage.getItem(storageKey);
  if (!key) {
    key = crypto.randomUUID();
    sessionStorage.setItem(storageKey, key);
  }
  const result = await send({ "Idempotency-Key": key });
  if (sessionStorage.getItem(storageKey) === key) sessionStorage.removeItem(storageKey);
  return result;
}
