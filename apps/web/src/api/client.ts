import createClient from "openapi-fetch";
import type { paths } from "./schema";

// Every number the UI renders comes from this client's responses (or the
// shared analysis package via the API) - never independently recomputed in
// front-end code (spec section 4/35).
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export const api = createClient<paths>({ baseUrl: API_BASE_URL });

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
