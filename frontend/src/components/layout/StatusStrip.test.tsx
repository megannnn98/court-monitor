import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { getStatusV1 } from "@/api/generated";
import { StatusStrip } from "@/components/layout/StatusStrip";

vi.mock("@/api/generated", () => ({ getStatusV1: vi.fn() }));

function answer(readOnly: boolean) {
  vi.mocked(getStatusV1).mockResolvedValue({
    data: {
      articles: 1,
      people: 1,
      result: 0,
      queue: { total: 0, pairs: 0, unclear_roles: 0, unclear_verdicts: 0, unnamed: 0, junk_holds: 0 },
      latest_monitoring_status: null,
      live_operation: null,
      next_action: "",
      read_only: readOnly
    },
    error: undefined,
    request: new Request("http://localhost/api/v1/status"),
    response: new Response(null, { status: 200 })
  } as Awaited<ReturnType<typeof getStatusV1>>);
}

function renderStrip() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <StatusStrip />
    </QueryClientProvider>
  );
}

it("says a demo stand is read-only", async () => {
  answer(true);
  renderStrip();

  expect((await screen.findByRole("note")).textContent).toContain("только просмотр");
});

it("says nothing of a demo elsewhere", async () => {
  answer(false);
  renderStrip();

  await screen.findByText("Публикации");
  expect(screen.queryByRole("note")).toBeNull();
});
