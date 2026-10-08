import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { getAskV1, postAskV1 } from "@/api/generated";
import { AskPage } from "@/pages/AskPage";
import { ok, renderPage } from "@/test/render";

vi.mock("@/api/generated", () => ({ getAskV1: vi.fn(), postAskV1: vi.fn() }));
const get = vi.mocked(getAskV1);
const post = vi.mocked(postAskV1);

const ANSWER = {
  id: 9,
  question: "Где суровее?",
  answer: "В Москве 7 лет.\nСм. [№ 5] и [12345].",
  outcome: "answered",
  asked_at: "2026-10-08T09:00:00Z",
  model: "deepseek",
  cost_usd: 0.0021,
  unverified: ["5"],
  results: [
    { tool: "stats", what: "приговоры по регионам", groups: [{ name: "Москва", cases: 1, mean_years: 7.5 }], small_groups: [], total: { name: "Всего", cases: 2 } },
    { tool: "search", what: "поиск: пикет", publications: [{ article_id: 3, title: "Пикет", source: "ОВД", published: "01.10.2026", snippet: "одиночный [[пикет]] у суда" }], total: 1 }
  ]
};
const PAGE = { spent_today: 0.12, budget: 1, max_question: 500, examples: ["Какие самые большие сроки давали заочно?"], mark_open: "[[", mark_close: "]]", answer: ANSWER, history: [{ id: 9, question: "Где суровее?", asked_at: "2026-10-08T09:00:00Z" }] };

beforeEach(() => {
  get.mockReset();
  post.mockReset();
  get.mockReturnValue(ok(PAGE) as never);
});

it("shows the answer with links, the warning and the counts under it", async () => {
  renderPage(<AskPage />);

  const answer = await screen.findByRole("region", { name: "Ответ" });
  expect(within(answer).getByText("В Москве 7 лет.")).toBeTruthy();
  expect(within(answer).getByRole("link", { name: "[№ 5]" }).getAttribute("href")).toBe("/articles/5");
  expect(within(answer).getByRole("link", { name: "[№ 12345]" }).getAttribute("href")).toBe("/articles/12345");
  expect(within(answer).getByText(/проверьте по таблицам: 5\./)).toBeTruthy();
  expect(within(answer).getByText("7,5")).toBeTruthy();
  expect(within(answer).getByText("пикет").tagName).toBe("MARK");
  expect(screen.getByText(/Сегодня потрачено \$0\.12 из \$1\.00/)).toBeTruthy();
});

it("asks with an example and opens the new answer", async () => {
  post.mockReturnValue(ok({ id: 10, note: null }) as never);
  renderPage(<AskPage />);

  fireEvent.click(await screen.findByRole("button", { name: "Какие самые большие сроки давали заочно?" }));
  fireEvent.click(screen.getByRole("button", { name: "Спросить" }));

  await waitFor(() => expect(post).toHaveBeenCalledWith({ body: { question: "Какие самые большие сроки давали заочно?" } }));
  await waitFor(() => expect(get).toHaveBeenLastCalledWith({ query: { q: 10 } }));
});

it("says why nothing was asked", async () => {
  post.mockReturnValue(ok({ id: null, note: "Дневной бюджет исчерпан." }) as never);
  renderPage(<AskPage />);

  fireEvent.change(await screen.findByLabelText("Вопрос"), { target: { value: "Сколько?" } });
  fireEvent.click(screen.getByRole("button", { name: "Спросить" }));

  expect(await screen.findByText("Дневной бюджет исчерпан.")).toBeTruthy();
});
