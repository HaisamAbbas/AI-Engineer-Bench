import { vi, afterEach } from "vitest";
import { api } from "../api/client";

/** Spies directly on the typed `api.GET` method (from openapi-fetch, typed
 * against the real, checked-in OpenAPI schema) rather than intercepting
 * network traffic - the real hook/query/render code under test still runs
 * unmodified, but this sidesteps environment-specific fetch-interception
 * fragility (MSW's node interceptors did not patch fetch reliably under
 * this jsdom + very-recent-Node combination; see git history for the
 * investigation). Route keys are the exact path template string each hook
 * passes to api.GET (e.g. "/v1/releases", "/v1/tasks/{slug}/revisions/{version}"),
 * unresolved - openapi-fetch itself would interpolate path params, but we
 * intercept before that, so matching on the literal template is exact and
 * doesn't need its own path-matching logic. */
type MockResult = { status?: number; data?: unknown; error?: unknown };
type Route = MockResult | ((params: unknown) => MockResult);

export function mockApi(routes: Partial<Record<string, Route>>) {
  const spy = vi.spyOn(api, "GET");
  spy.mockImplementation(((path: string, options?: { params?: unknown }) => {
    const route = routes[path];
    if (!route) {
      throw new Error(`mockApi: no route registered for GET ${path}`);
    }
    const resolved = typeof route === "function" ? route(options?.params) : route;
    const status = resolved.status ?? (resolved.error !== undefined ? 404 : 200);
    return Promise.resolve({
      data: resolved.data,
      error: resolved.error,
      response: new Response(null, { status }),
    });
  }) as typeof api.GET);
  return spy;
}

afterEach(() => {
  vi.restoreAllMocks();
});
