import { QueryClient } from "@tanstack/react-query";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { decidePoliticsV1, decideRoleV1, listUnclearPoliticsV1, listUnclearRolesV1 } from "@/api/generated";
import { dossierPath } from "@/lib/navigation";
import { ReviewPage } from "@/pages/ReviewPage";
import { ok, renderPage } from "@/test/render";

vi.mock("@/api/generated", () => ({
  listUnclearRolesV1: vi.fn(),
  decideRoleV1: vi.fn(),
  listUnclearPoliticsV1: vi.fn(),
  decidePoliticsV1: vi.fn()
}));
const roles = vi.mocked(listUnclearRolesV1);
const decideRole = vi.mocked(decideRoleV1);
const politics = vi.mocked(listUnclearPoliticsV1);
const decidePolitics = vi.mocked(decidePoliticsV1);

const ROLES = {
  title: "Неясная роль в деле",
  explanation: "Шаг 4 не понял.",
  choices: [
    { value: "figurant", label: "Фигурант" },
    { value: "mentioned", label: "Только упомянут" }
  ],
  items: [{ key: "фёдор сирош", name: "Сирош Фёдор", reason: "модель не ответила" }]
};

beforeEach(() => {
  for (const mock of [roles, decideRole, politics, decidePolitics]) {
    mock.mockReset();
  }
});

it("decides a role and reads the station again", async () => {
  roles.mockReturnValue(ok(ROLES) as never);
  decideRole.mockReturnValue(ok({ key: "фёдор сирош", decision: "figurant" }) as never);

  renderPage(<ReviewPage kind="roles" />);

  expect((await screen.findByRole("link", { name: "Сирош Фёдор" })).getAttribute("href")).toBe(dossierPath("фёдор сирош"));
  fireEvent.click(screen.getByRole("button", { name: "Фигурант" }));
  await waitFor(() => expect(decideRole).toHaveBeenCalledWith({ body: { key: "фёдор сирош", role: "figurant" } }));
  await waitFor(() => expect(roles).toHaveBeenCalledTimes(2));
});

it("decides politics and shows the refusal", async () => {
  politics.mockReturnValue(ok({ ...ROLES, title: "Неясная политичность", choices: [{ value: "political", label: "Политическое" }] }) as never);
  decidePolitics.mockReturnValue(
    Promise.resolve({
      data: undefined,
      error: { error: { code: "csrf_refused", message: "cross-origin request refused: https://evil.example", request_id: "r" } },
      response: new Response(null, { status: 403 })
    }) as never
  );

  renderPage(<ReviewPage kind="politics" />);

  fireEvent.click(await screen.findByRole("button", { name: "Политическое" }));
  expect(await screen.findByText("cross-origin request refused: https://evil.example")).toBeTruthy();
  expect(decidePolitics).toHaveBeenCalledWith({ body: { key: "фёдор сирош", verdict: "political" } });
});

it("says when nothing is open", async () => {
  roles.mockReturnValue(ok({ ...ROLES, items: [] }) as never);

  renderPage(<ReviewPage kind="roles" />);

  expect(await screen.findByText("Неясная роль в деле: открытых случаев нет.")).toBeTruthy();
});

it("leaves «Работа» to be read again: the closed review must not stay there", async () => {
  roles.mockReturnValue(ok(ROLES) as never);
  decideRole.mockReturnValue(ok({ key: "фёдор сирош", decision: "figurant" }) as never);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 30_000 } } });
  // «Работа» was open a moment ago: its queues are in the cache, fresh for 30 s.
  client.setQueryData(["cycle"], { attention: { key: "roles", count: 1 } });
  client.setQueryData(["status"], { queue: { total: 1 } });

  renderPage(<ReviewPage kind="roles" />, { client });
  fireEvent.click(await screen.findByRole("button", { name: "Фигурант" }));

  await waitFor(() => expect(client.getQueryState(["cycle"])?.isInvalidated).toBe(true));
  expect(client.getQueryState(["status"])?.isInvalidated).toBe(true);
});
