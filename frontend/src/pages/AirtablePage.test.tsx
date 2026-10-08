import { QueryClient } from "@tanstack/react-query";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { getAirtableV1, refreshRosfinV1, syncReferenceListsV1 } from "@/api/generated";
import { AirtablePage } from "@/pages/AirtablePage";
import { failed, ok, renderPage } from "@/test/render";

vi.mock("@/api/generated", () => ({ getAirtableV1: vi.fn(), syncReferenceListsV1: vi.fn(), refreshRosfinV1: vi.fn() }));
const get = vi.mocked(getAirtableV1);
const sync = vi.mocked(syncReferenceListsV1);
const refresh = vi.mocked(refreshRosfinV1);

const OVERVIEW = {
  configured: true,
  reason: "",
  mode_note: "Список читается по публичным ссылкам Airtable.",
  missing: [],
  source_column: "Публичная ссылка",
  lists: [
    { name: "known_persons", label: "Найденные люди", count: 13943, source: "https://airtable.com/shr1" },
    { name: "officials", label: "Должностные лица", count: 12, source: "" }
  ],
  official: { snapshot_id: 8, snapshot_date: "2026-10-06T10:00:00Z", entry_count: 23092, match_count: 1095 }
};

beforeEach(() => {
  get.mockReset();
  sync.mockReset();
  refresh.mockReset();
  get.mockReturnValue(ok(OVERVIEW) as never);
});

it("shows the lists, where they come from and the published list", async () => {
  renderPage(<AirtablePage />);

  const stock = await screen.findByRole("region", { name: "Что сейчас в базе" });
  expect(within(stock).getByText("13 943")).toBeTruthy();
  expect(within(stock).getByRole("link", { name: "Должностные лица" }).getAttribute("href")).toBe("/officials");
  expect(screen.getByText(/Снимок #8 от/)).toBeTruthy();
  expect(screen.getByText(OVERVIEW.mode_note)).toBeTruthy();
});

it("syncs and shows what each list did, a failed one said", async () => {
  sync.mockReturnValue(
    ok({
      status: "partial",
      mode: "share",
      started_at: "2026-10-08T09:00:00Z",
      tables: {
        known_persons: { created: 31, updated: 14, unchanged: 13898, errors: 0, received: 13943, status: "success", error: null },
        articles: { created: 0, updated: 0, unchanged: 0, errors: 1, received: 0, status: "error", error: "таблица недоступна" }
      }
    }) as never
  );
  renderPage(<AirtablePage />);

  fireEvent.click(await screen.findByRole("button", { name: "Синхронизировать Airtable" }));

  expect(await screen.findByText("ошибка: таблица недоступна")).toBeTruthy();
  expect(screen.getByText("31")).toBeTruthy();
});

it("cannot sync when nothing is set up, and says why a refresh was refused", async () => {
  get.mockReturnValue(ok({ ...OVERVIEW, configured: false, reason: "нет ссылок", official: null }) as never);
  refresh.mockReturnValue(failed(409, "Идёт другой запуск.") as never);
  renderPage(<AirtablePage />);

  expect(((await screen.findByRole("button", { name: "Синхронизировать Airtable" })) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByText("Синхронизация недоступна: нет ссылок")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Обновить перечень и сверить с РФМ" }));
  await waitFor(() => expect(refresh).toHaveBeenCalled());
  expect(await screen.findByText("Идёт другой запуск.")).toBeTruthy();
});

it("a started check is read again by the journal, «Работа» and the strip", async () => {
  // Each was read while nothing ran: none of them polls for a run it has not seen.
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 30_000 } } });
  const seen = [["operations", "runs"], ["cycle"], ["status"], ["legacy", "/ui/runs", ""]];
  for (const key of seen) {
    client.setQueryData(key, []);
  }
  refresh.mockReturnValue(ok({ run_id: 73 }) as never);
  renderPage(<AirtablePage />, { client });

  fireEvent.click(await screen.findByRole("button", { name: "Обновить перечень и сверить с РФМ" }));

  await waitFor(() => expect(seen.map((key) => client.getQueryState(key)?.isInvalidated)).toEqual([true, true, true, true]));
});
