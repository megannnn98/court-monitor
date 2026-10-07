import { screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { listSentencesV1 } from "@/api/generated";
import { SentencesPage } from "@/pages/SentencesPage";
import { failed, ok, renderPage } from "@/test/render";

vi.mock("@/api/generated", () => ({ listSentencesV1: vi.fn() }));
const list = vi.mocked(listSentencesV1);

const ROW = {
  row_ids: [4, 9],
  person: "Петров Иван",
  region: "Москва",
  kind_label: "колония",
  in_absentia: true,
  term: "7 г.",
  sentenced_on: "2026-09-24",
  reason_label: "антивоенные высказывания",
  reason_text: "посты о войне",
  article_id: 77,
  title: "Два приговора",
  source: "news",
  more_publications: 1,
  quote: "приговорил Петров Иван"
};
const PAGE = {
  view: "",
  reason: "political",
  region: "",
  items: [ROW],
  total: 1,
  page: 1,
  page_size: 50,
  read: 12,
  cases: 2,
  hidden: 3,
  reasons: [
    { value: "", label: "Все дела" },
    { value: "political", label: "Все политические" }
  ],
  regions: ["Москва"]
};

beforeEach(() => list.mockReset());

it("shows the cases with their words and sources", async () => {
  list.mockReturnValue(ok(PAGE) as never);

  renderPage(<SentencesPage />);

  expect(await screen.findByText("Петров Иван")).toBeTruthy();
  for (const text of ["колония (заочно)", "7 г.", "посты о войне", "news и ещё 1", "«приговорил Петров Иван»", "Убранные: 3"]) {
    expect(screen.getByText(text)).toBeTruthy();
  }
  expect(screen.getByRole("link", { name: "Два приговора" }).getAttribute("href")).toBe("/articles/77");
  expect(list).toHaveBeenCalledWith({ query: { reason: "political", region: "", view: "", page: 1 } });
});

it("sends «все дела» as an empty reason and lists what was taken out", async () => {
  list.mockReturnValue(ok({ ...PAGE, view: "hidden", items: [], total: 0 }) as never);

  renderPage(<SentencesPage />, { url: "/?reason=any&region=Москва&view=hidden" });

  expect(await screen.findByText("Ничего не убрано.")).toBeTruthy();
  expect(list).toHaveBeenCalledWith({ query: { reason: "", region: "Москва", view: "hidden", page: 1 } });
});

it("shows the API's own error", async () => {
  list.mockReturnValue(failed(500, "sentences unreadable") as never);

  renderPage(<SentencesPage />);

  expect(await screen.findByText("sentences unreadable")).toBeTruthy();
});
