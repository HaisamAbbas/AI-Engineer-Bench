import createClient from "openapi-fetch";
import type { paths } from "./schema";
import { accessToken } from "./oidc";

// Every number the UI renders comes from this client's responses (or the
// shared analysis package via the API) - never independently recomputed in
// front-end code (spec section 4/35).
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export const api = createClient<paths>({ baseUrl: API_BASE_URL });

let accessTokenProvider: (() => Promise<string | undefined> | string | undefined) | undefined = accessToken;
export function setAccessTokenProvider(provider: (() => Promise<string | undefined> | string | undefined) | undefined) {
  accessTokenProvider = provider;
}

api.use({
  async onRequest({ request }) {
    const token = await accessTokenProvider?.();
    if (token) request.headers.set("Authorization", `Bearer ${token}`);
    return request;
  },
});

export async function downloadAuthorizedArtifact(trialId: string, artifactRefId: string, filename: string): Promise<void> {
  const token = await accessTokenProvider?.();
  const response = await fetch(
    `${API_BASE_URL}/v1/trials/${encodeURIComponent(trialId)}/artifacts/${encodeURIComponent(artifactRefId)}/download`,
    { headers: token ? { Authorization: `Bearer ${token}` } : undefined },
  );
  if (!response.ok) throw new ApiRequestError(response.status, "artifact_download_failed", "artifact download failed", undefined);
  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(objectUrl);
}

export class ApiRequestError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public requestId: string | undefined,
  ) {
    super(message);
  }
}

interface ErrorEnvelope {
  error: { code: string; message: string; request_id: string; field_errors: Record<string, string>; retryable: boolean };
}

/** Turns openapi-fetch's {data,error} result into a thrown ApiRequestError on
 * failure, so TanStack Query's own error/retry handling applies uniformly -
 * every route in this app goes through the same typed error envelope
 * (spec section 33: "typed errors ... request ID"). Takes the *pending*
 * openapi-fetch call directly (not an already-awaited result) so every
 * query function can just be `() => unwrap(api.GET(...))` and still get a
 * correctly inferred, non-`unknown` return type. */
export async function unwrap<T>(pending: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  const result = await pending;
  if (result.error !== undefined) {
    const envelope = result.error as ErrorEnvelope;
    const body = envelope?.error;
    throw new ApiRequestError(
      result.response.status,
      body?.code ?? "error",
      body?.message ?? "request failed",
      body?.request_id,
    );
  }
  if (result.data === undefined) {
    throw new ApiRequestError(result.response.status, "error", "empty response", undefined);
  }
  return result.data;
}
