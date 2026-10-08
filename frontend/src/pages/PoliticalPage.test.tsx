import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { listPoliticalV1, markPoliticalDoneV1 } from "@/api/generated";
import { PoliticalPage } from "@/pages/PoliticalPage";
import { dossierPath } from "@/lib/navigation";
import { failed, ok, renderPage } from "@/test/render";

vi.mock("@/api/generated", () => ({ listPoliticalV1: vi.fn(), markPoliticalDoneV1: vi.fn() }));
const list = vi.mocked(listPoliticalV1);
const mark = vi.mocked(markPoliticalDoneV1);

const ROW = {
  key: "анна смирнова",
  name: "Смирнова Анна",
  url: "/ui/investigations/x",
  unnamed: false,
  done: false,
  news_kind: "sentence",
  news_label: "Приговор",
  news_reason: "приговорили",
  known: null,
  not_in_base: true,
  regions: "Москва",
  rf_level: "full",
  rf_label: "в перечне РФМ",
  rf_entry: "СМИРНОВА АННА ПЕТРОВНА, 01.02.1990 г.р.",
  rf_included: "запись перечня включена 01.03.2025",
  listing: "статья перечня: 205.2",
  awaited: false,
  articles: [
    { article: "205.2", political: true },
    { article: "158", political: false }
  ],
  basis: "модель: так про Анна Смирнова",
  basis_quote: "цитата",
  memorial: null,
  first_published_at: "2026-01-01T00:00:00Z",
  last_published_at: "2026-09-27T00:00:00Z",
  links: [
    { title: "Приговор Смирновой", url: "https://ovd.example/2", source: "ОВД-Инфо" },
    { title: "Опасная ссылка", url: "javascript:alert(1)", source: "x" }
  ]
};
const EXPORT_URL = "/ui/political/export.xlsx?months=0&date_from=&date_to=&news=all&known=all&done=hide&who=all&rfm=all";
const PAGE = {
  items: [ROW],
  total: 1,
  page: 1,
  page_size: 100,
  done_total: 6,
  awaited: 2,
  base_loaded: true,
  export_url: EXPORT_URL,
  periods: [
    { value: "0", label: "За всё время" },
    { value: "3", label: "3 месяца" }
  ],
  news: [{ value: "all", label: "Любая свежая новость" }],
  known: [
    { value: "all", label: "Любые" },
    { value: "none", label: "Нет в базе", count: 1 }
  ],
  who: [{ value: "all", label: "Все" }],
  rfm: [
    { value: "all", label: "Все" },
    { value: "awaited", label: "Ждём в перечне", count: 2 }
  ]
};
const QUERY = { months: 0, date_from: "", date_to: "", news: "all", known: "all", done: "hide", who: "all", rfm: "all", page: 1 };

beforeEach(() => {
  list.mockReset();
  mark.mockReset();
});

it("shows the result as the legacy page does", async () => {
  list.mockReturnValue(ok(PAGE) as never);

  renderPage(<PoliticalPage />);

  expect((await screen.findByRole("link", { name: "Смирнова Анна" })).getAttribute("href")).toBe(dossierPath("анна смирнова"));
  for (const text of ["в перечне РФМ", "запись перечня включена 01.03.2025", "нет в базе", "модель: так про Анна Смирнова", "статья перечня: 205.2"]) {
    expect(screen.getByText(text)).toBeTruthy();
  }
  expect(screen.getByText("205.2").tagName).toBe("B");
  expect(screen.getByRole("link", { name: "Приговор Смирновой" }).getAttribute("rel")).toBe("noopener noreferrer");
  expect(screen.queryByRole("link", { name: "Опасная ссылка" })).toBeNull();
  expect(screen.getByText(/Показать обработанных \(6\)/)).toBeTruthy();
  expect(screen.getByRole("button", { name: "За всё время" }).getAttribute("aria-pressed")).toBe("true");
  expect(list).toHaveBeenCalledWith({ query: QUERY });
});

it("sends the period and the filters of the address", async () => {
  list.mockReturnValue(ok({ ...PAGE, items: [], total: 0, base_loaded: false, known: [] }) as never);

  renderPage(<PoliticalPage />, { url: "/?date_from=2026-09-01&date_to=2026-09-30&rfm=awaited&done=show" });

  expect(await screen.findByText("За этот период и с этими фильтрами никого нет.")).toBeTruthy();
  expect(list).toHaveBeenCalledWith({
    query: { ...QUERY, date_from: "2026-09-01", date_to: "2026-09-30", rfm: "awaited", done: "show" }
  });
  expect(screen.queryByText("В базе Airtable")).toBeNull();
});

it("shows the API's own error", async () => {
  list.mockReturnValue(failed(500, "known base unreadable") as never);

  renderPage(<PoliticalPage />);

  expect(await screen.findByText("known base unreadable")).toBeTruthy();
});

it("ticks «обработано» and reads the list again", async () => {
  list.mockReturnValue(ok(PAGE) as never);
  mark.mockReturnValue(ok({ key: "анна смирнова", done: true }) as never);

  renderPage(<PoliticalPage />);
  fireEvent.click(await screen.findByRole("checkbox", { name: "Обработано: Смирнова Анна" }));

  await waitFor(() => expect(mark).toHaveBeenCalledWith({ body: { key: "анна смирнова", done: true } }));
  await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
  expect(screen.getByRole("link", { name: "Скачать Excel" }).getAttribute("href")).toBe(EXPORT_URL);
});

it("keeps the file of the rows shown while the next filters are read", async () => {
  list.mockReturnValueOnce(ok(PAGE) as never).mockReturnValue(new Promise(() => {}) as never);

  renderPage(<PoliticalPage />);
  fireEvent.click(await screen.findByRole("button", { name: "3 месяца" }));

  expect(list).toHaveBeenLastCalledWith({ query: { ...QUERY, months: 3 } });
  expect(screen.getByText("Смирнова Анна")).toBeTruthy();
  expect(screen.getByRole("link", { name: "Скачать Excel" }).getAttribute("href")).toBe(EXPORT_URL);
});

it("says why a tick was refused", async () => {
  list.mockReturnValue(ok(PAGE) as never);
  mark.mockReturnValue(failed(404, "Человек не найден") as never);

  renderPage(<PoliticalPage />);
  fireEvent.click(await screen.findByRole("checkbox", { name: "Обработано: Смирнова Анна" }));

  expect(await screen.findByText("Человек не найден")).toBeTruthy();
});
