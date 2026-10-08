import { fireEvent, screen, waitFor, within } from "@testing-library/react";
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
  ],
  queues: [
    { value: "all", label: "Все", count: 1 },
    { value: "sentence", label: "Приговоры", count: 1 },
    { value: "unnamed", label: "Без имени", count: 0 },
    { value: "awaited", label: "Ждём в перечне", count: 2 },
    { value: "done", label: "Обработанные", count: 6 }
  ]
};
const QUERY = { months: 0, date_from: "", date_to: "", queue: "all", page: 1 };
const OTHER = {
  ...ROW,
  key: "иван иванов",
  name: "Иванов Иван",
  rf_label: null,
  rf_entry: "",
  rf_included: null,
  listing: "ждём в перечне: 205.2",
  awaited: true,
  basis: "модель: про Иванова",
  links: [{ title: "Дело Иванова", url: "https://ovd.example/3", source: "ОВД-Инфо" }]
};

beforeEach(() => {
  list.mockReset();
  mark.mockReset();
});

it("shows the queues, a short table and the first person in the panel", async () => {
  list.mockReturnValue(ok(PAGE) as never);

  renderPage(<PoliticalPage />);

  expect((await screen.findAllByRole("link", { name: "Смирнова Анна" }))[0].getAttribute("href")).toBe(dossierPath("анна смирнова"));
  expect(list).toHaveBeenCalledWith({ query: QUERY });
  expect(screen.getByRole("button", { name: "Все 1" }).getAttribute("aria-pressed")).toBe("true");
  expect(screen.getByRole("button", { name: "Обработанные 6" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "За всё время" }).getAttribute("aria-pressed")).toBe("true");
  const panel = screen.getByRole("complementary", { name: "Подробно: Смирнова Анна" });
  for (const text of ["модель: так про Анна Смирнова", "цитата", "запись перечня включена 01.03.2025", "статья перечня: 205.2"]) {
    expect(within(panel).getByText(text)).toBeTruthy();
  }
  expect(within(panel).getByRole("link", { name: "Приговор Смирновой" }).getAttribute("rel")).toBe("noopener noreferrer");
  expect(within(panel).queryByRole("link", { name: "Опасная ссылка" })).toBeNull();
  // The table keeps the marks, not the long texts.
  const table = screen.getByRole("table");
  expect(within(table).getAllByText("в перечне РФМ")).toHaveLength(1);
  expect(within(table).getByText("нет в базе")).toBeTruthy();
  expect(within(table).getByText("205.2").tagName).toBe("B");
  expect(within(table).queryByText("модель: так про Анна Смирнова")).toBeNull();
});

it("shows the person chosen in the table", async () => {
  list.mockReturnValue(ok({ ...PAGE, items: [ROW, OTHER], total: 2 }) as never);

  renderPage(<PoliticalPage />);
  await screen.findByRole("complementary", { name: "Подробно: Смирнова Анна" });
  // The second row's news cell: a click anywhere off the links and the tick.
  fireEvent.click(within(screen.getAllByRole("row")[2]).getAllByRole("cell")[2]);

  const panel = await screen.findByRole("complementary", { name: "Подробно: Иванов Иван" });
  expect(within(panel).getByText("модель: про Иванова")).toBeTruthy();
  expect(within(panel).getByRole("link", { name: "Дело Иванова" })).toBeTruthy();
});

it("opens a queue: its own column, the period kept", async () => {
  list.mockReturnValue(ok({ ...PAGE, items: [OTHER] }) as never);

  renderPage(<PoliticalPage />, { url: "/?months=3" });
  fireEvent.click(await screen.findByRole("button", { name: "Ждём в перечне 2" }));

  await waitFor(() => expect(list).toHaveBeenLastCalledWith({ query: { ...QUERY, months: 3, queue: "awaited" } }));
  expect(await screen.findByRole("columnheader", { name: "Перечень РФМ" })).toBeTruthy();
});

it("sends the dates of the address", async () => {
  list.mockReturnValue(ok({ ...PAGE, items: [], total: 0 }) as never);

  renderPage(<PoliticalPage />, { url: "/?date_from=2026-09-01&date_to=2026-09-30&queue=done" });

  expect(await screen.findByText("За этот период в этой очереди никого нет.")).toBeTruthy();
  expect(list).toHaveBeenCalledWith({ query: { ...QUERY, date_from: "2026-09-01", date_to: "2026-09-30", queue: "done" } });
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
  expect(screen.getAllByText("Смирнова Анна").length).toBeGreaterThan(0);
  expect(screen.getByRole("link", { name: "Скачать Excel" }).getAttribute("href")).toBe(EXPORT_URL);
});

it("says why a tick was refused", async () => {
  list.mockReturnValue(ok(PAGE) as never);
  mark.mockReturnValue(failed(404, "Человек не найден") as never);

  renderPage(<PoliticalPage />);
  fireEvent.click(await screen.findByRole("checkbox", { name: "Обработано: Смирнова Анна" }));

  expect(await screen.findByText("Человек не найден")).toBeTruthy();
});

it("on a narrow screen opens the person tapped over the table, and no one before", async () => {
  // A phone: the panel has no room beside the table.
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: false, media: query, addEventListener: () => undefined, removeEventListener: () => undefined }));
  try {
    list.mockReturnValue(ok({ ...PAGE, items: [ROW, OTHER], total: 2 }) as never);
    renderPage(<PoliticalPage />);

    const rows = await screen.findAllByRole("row");
    expect(screen.queryByRole("complementary", { name: /Подробно/ })).toBeNull();
    fireEvent.click(within(rows[2]).getAllByRole("cell")[2]);

    const sheet = await screen.findByRole("dialog");
    expect(within(sheet).getByRole("complementary", { name: "Подробно: Иванов Иван" })).toBeTruthy();
  } finally {
    vi.unstubAllGlobals();
  }
});
