import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { clearUnnamedV1, keepUnnamedV1, listUnnamedV1, rejectUnnamedV1, resolveUnnamedV1 } from "@/api/generated";
import { UnnamedPage } from "@/pages/UnnamedPage";
import { failed, ok, renderPage } from "@/test/render";

vi.mock("@/api/generated", () => ({
  listUnnamedV1: vi.fn(),
  keepUnnamedV1: vi.fn(),
  rejectUnnamedV1: vi.fn(),
  resolveUnnamedV1: vi.fn(),
  clearUnnamedV1: vi.fn()
}));
const list = vi.mocked(listUnnamedV1);
const keep = vi.mocked(keepUnnamedV1);
const reject = vi.mocked(rejectUnnamedV1);
const resolve = vi.mocked(resolveUnnamedV1);
const clear = vi.mocked(clearUnnamedV1);

const CARD = {
  key: "k1",
  article_id: 7,
  start_offset: 0,
  end_offset: 40,
  quote: "В Тюмени задержан 17-летний житель города",
  published_at: "2024-11-25T00:00:00Z",
  facts: "17 лет · мужчина · Тюмень · задержание · ст. 205",
  explanation: "задержан по делу о теракте",
  has_age: true,
  resolution: "",
  identified_as: null,
  note: null,
  rf_candidates: [
    {
      key: "пуртов егор владимирович|2007-02-17",
      full_name: "ПУРТОВ ЕГОР ВЛАДИМИРОВИЧ",
      display_name: "Пуртов Егор Владимирович",
      rf_name: "пуртов егор владимирович",
      birth_date: "2007-02-17",
      birth_place: "Г. ТЮМЕНЬ",
      reasons: ["родился в Тюмени"],
      seen: "в перечне с 01.12.2024 или раньше",
      decision: null
    }
  ],
  rf_total: 1,
  rf_snapshot_date: "2024-12-01T00:00:00Z",
  named_candidates: [],
  base_candidates: [],
  base_total: 0,
  courts: [{ court: "Тюменский областной суд", cases: 3, site: "https://court.example" }],
  courts_total: 3
};
const PAGE = {
  statuses: [
    { value: "open", label: "Не разобраны", count: 1 },
    { value: "found", label: "Опознаны", count: 0 },
    { value: "all", label: "Все", count: 1 }
  ],
  items: [CARD],
  total: 1,
  page: 1,
  page_size: 20,
  none_yet: false,
  people: [],
  rf_entries: [],
  reverse: [],
  screened: [
    {
      key: "s1",
      article_id: 8,
      start_offset: 1,
      end_offset: 9,
      quote: "Осуждён за кражу",
      published_at: null,
      facts: "30 лет · приговор · ст. 158",
      reason: "обычная уголовная статья",
      explanation: "кража"
    }
  ]
};
const QUERY = { status: "open", page: 1, person_q: "", rf_q: "", rf_key: "" };

beforeEach(() => {
  for (const mock of [list, keep, reject, resolve, clear]) {
    mock.mockReset();
  }
  list.mockReturnValue(ok(PAGE) as never);
});

it("shows a card: the sentence, what it tells and the candidates of the list", async () => {
  renderPage(<UnnamedPage />);

  const card = await screen.findByRole("article", { name: CARD.quote });
  expect(within(card).getByRole("link", { name: CARD.quote }).getAttribute("href")).toBe("/articles/7?start=0&end=40");
  for (const text of [CARD.facts, "не разобран", "ПУРТОВ ЕГОР ВЛАДИМИРОВИЧ", "в перечне с 01.12.2024 или раньше"]) {
    expect(within(card).getByText(text)).toBeTruthy();
  }
  expect(within(card).getByRole("link", { name: "сайт суда" }).getAttribute("rel")).toBe("noopener noreferrer");
  expect(screen.getByRole("button", { name: "Не разобраны 1" }).getAttribute("aria-pressed")).toBe("true");
  expect(list).toHaveBeenCalledWith({ query: QUERY });
});

it("says «Это он» with the entry's name and birth date, and reads the page again", async () => {
  resolve.mockReturnValue(ok({ figurant: "k1" }) as never);
  renderPage(<UnnamedPage />);

  const card = await screen.findByRole("article", { name: CARD.quote });
  fireEvent.click(within(card).getByRole("button", { name: "Это он" }));

  await waitFor(() =>
    expect(resolve).toHaveBeenCalledWith({
      body: {
        figurant: "k1",
        resolution: "rf_entry",
        normalized_name: "Пуртов Егор Владимирович",
        rf_name: "пуртов егор владимирович",
        rf_birth_date: "2007-02-17"
      }
    })
  );
  await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
});

it("says «Не он» of the entry and shows a refusal", async () => {
  reject.mockReturnValue(failed(400, "Не выбран человек из перечня") as never);
  renderPage(<UnnamedPage />);

  const card = await screen.findByRole("article", { name: CARD.quote });
  fireEvent.click(within(card).getByRole("button", { name: "Не он" }));

  expect(await within(card).findByText("Не выбран человек из перечня")).toBeTruthy();
  expect(reject).toHaveBeenCalledWith({ body: { figurant: "k1", candidate: "пуртов егор владимирович|2007-02-17" } });
});

it("opens another tab and takes one set aside back", async () => {
  keep.mockReturnValue(ok({ figurant: "s1" }) as never);
  renderPage(<UnnamedPage />);

  fireEvent.click(await screen.findByRole("button", { name: "Опознаны 0" }));
  await waitFor(() => expect(list).toHaveBeenLastCalledWith({ query: { ...QUERY, status: "found" } }));

  fireEvent.click(screen.getByText("Отсеяно автоматически (1)"));
  fireEvent.click(screen.getByRole("button", { name: "Вернуть на разбор" }));
  await waitFor(() => expect(keep).toHaveBeenCalledWith({ body: { figurant: "s1" } }));
});

it("says why the page is empty", async () => {
  list.mockReturnValue(ok({ ...PAGE, items: [], total: 0, none_yet: true, screened: [] }) as never);
  renderPage(<UnnamedPage />);

  expect(await screen.findByText(/Безымянных фигурантов пока нет/)).toBeTruthy();
});
