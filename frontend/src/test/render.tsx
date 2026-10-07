import type { ReactElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

/** One page under its route, with a fresh query cache that does not retry. */
export function renderPage(element: ReactElement, { path = "/", url = "/" }: { path?: string; url?: string } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[url]}>
        <Routes>
          <Route path={path} element={element} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

/** What the generated client resolves to: data on success, the parsed body on an error. */
export function ok<T>(data: T) {
  return Promise.resolve({ data, error: undefined, request: new Request("http://test"), response: new Response(null, { status: 200 }) });
}

export function failed(status: number, detail: unknown) {
  return Promise.resolve({
    data: undefined,
    error: { detail },
    request: new Request("http://test"),
    response: new Response(null, { status })
  });
}
