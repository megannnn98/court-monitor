import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { holdHeldV1, junkAllHeldV1, junkHeldV1, listJunkHoldsV1, reextractHeldV1, releaseHeldV1, unreleaseHeldV1 } from "@/api/generated";
import { JunkHoldsPage } from "@/pages/JunkHoldsPage";
import { failed, ok, renderPage } from "@/test/render";

vi.mock("@/api/generated", () => ({
  listJunkHoldsV1: vi.fn(),
  junkHeldV1: vi.fn(),
  junkAllHeldV1: vi.fn(),
  releaseHeldV1: vi.fn(),
  unreleaseHeldV1: vi.fn(),
  holdHeldV1: vi.fn(),
  reextractHeldV1: vi.fn()
}));
const list = vi.mocked(listJunkHoldsV1);
const junk = vi.mocked(junkHeldV1);
const junkAll = vi.mocked(junkAllHeldV1);
const reextract = vi.mocked(reextractHeldV1);

function held(id: number, title: string) {
  return {
    article_id: id,
    status: "held",
    title,
    published_at: "2026-09-25T00:00:00Z",
    source: "news",
    url: "https://news.example/1",
    score: 0.81,
    cutoff: 0.52,
    events: null,
    start: "Начало текста",
    reason: "оценка выше порога — не доказательство уголовного дела",
    note: null,
    note_label: ""
  };
}
const PAGE = {
  status: "held",
  statuses: [
    { value: "held", label: "На проверке", count: 3 },
    { value: "model_junk", label: "Модель считает мусором", count: 0 },
    { value: "released", label: "Выпущено в работу", count: 0 },
    { value: "junk", label: "Отмечены как мусор", count: 0 }
  ],
  stories: [{ articles: [held(1, "Арестовали активиста"), held(2, "Активиста арестовали")] }, { articles: [held(3, "Выставка")] }],
  article_ids: [1, 2, 3],
  page: 1,
  pages: 1
};

beforeEach(() => {
  for (const mock of [list, junk, junkAll, reextract, vi.mocked(releaseHeldV1), vi.mocked(unreleaseHeldV1), vi.mocked(holdHeldV1)]) {
    mock.mockReset();
  }
  list.mockReturnValue(ok(PAGE) as never);
});

it("shows one story together with one button for all, and a card alone", async () => {
  junkAll.mockReturnValue(ok({ articles: [1, 2], released: false }) as never);
  renderPage(<JunkHoldsPage />);

  const story = await screen.findByRole("region", { name: "Одна новость" });
  expect(within(story).getAllByRole("article")).toHaveLength(2);
  fireEvent.click(within(story).getByRole("button", { name: "Мусор — все 2" }));

  await waitFor(() => expect(junkAll).toHaveBeenCalledWith({ body: { articles: [1, 2] } }));
  await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
  expect(screen.getByRole("article", { name: "Выставка" })).toBeTruthy();
  expect(list).toHaveBeenCalledWith({ query: { page: 1 } });
});

it("says «Мусор» of one and shows a refusal", async () => {
  junk.mockReturnValue(failed(404, "Статья не на проверке") as never);
  renderPage(<JunkHoldsPage />);

  const card = await screen.findByRole("article", { name: "Выставка" });
  fireEvent.click(within(card).getByRole("button", { name: "Мусор" }));

  await waitFor(() => expect(junk).toHaveBeenCalledWith({ body: { article: 3 } }));
  expect(await screen.findByText("Статья не на проверке")).toBeTruthy();
});

it("says when extracting again returned an article to work", async () => {
  reextract.mockReturnValue(ok({ articles: [3], released: true }) as never);
  renderPage(<JunkHoldsPage />);

  const card = await screen.findByRole("article", { name: "Выставка" });
  fireEvent.click(within(card).getByRole("button", { name: "Извлечь заново" }));

  expect(await screen.findByText(/найдено уголовное событие, она возвращена в работу/)).toBeTruthy();
});

it("opens the model's list with its one press for everything", async () => {
  list.mockReturnValue(ok({ ...PAGE, status: "model_junk", stories: [{ articles: [held(3, "Выставка")] }], article_ids: [3] }) as never);
  renderPage(<JunkHoldsPage />, { url: "/?status=model_junk" });

  expect(await screen.findByRole("button", { name: "Мусор — все 1" })).toBeTruthy();
  expect(list).toHaveBeenCalledWith({ query: { status: "model_junk", page: 1 } });
});
