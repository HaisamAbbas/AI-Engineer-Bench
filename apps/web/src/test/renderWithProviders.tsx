import type { ReactElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { render } from "@testing-library/react";

/** Every page test renders through the real router + real QueryClient (with
 * retries disabled so error states appear immediately instead of after
 * TanStack Query's default backoff), against MSW-mocked network responses -
 * the actual page/hook/fetch code runs unmodified. */
export function renderWithProviders(
  element: ReactElement,
  { route = "/", path = "/" }: { route?: string; path?: string } = {},
) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[route]}>
        <Routes>
          <Route path={path} element={element} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
