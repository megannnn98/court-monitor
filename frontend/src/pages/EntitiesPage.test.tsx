import { screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { listEntitiesV1 } from "@/api/generated";
import { EntitiesPage } from "@/pages/EntitiesPage";
import { failed, ok, renderPage } from "@/test/render";

vi.mock("@/api/generated", () => ({ listEntitiesV1: vi.fn() }));
const list = vi.mocked(listEntitiesV1);

const PAGE = {
  total: 1,
  page: 1,
  page_size: 100,
  hidden_in_list: 4,
  hidden_maybe_listed: 2,
  roles_known: true,
  roles: [
    { value: "figurant", label: "Фигуранты дел" },
    { value: "all", label: "Все роли" }
  ],
  verdicts: [{ value: "all", label: "Любой вердикт" }],
  regions: ["Москва"],
  items: [
    {
      id: 3,
      key: "александр моор",
      name: "Моор Александр",
      name_source_label: "ИИ",
      rf_level: "full",
      rf_label: "в перечне",
      role: "figurant",
      role_label: "фигурант дела",
      verdict: "political",
      verdict_label: "политическое",
      regions: ["Москва"],
      articles: [
        { article: "205.2", shared: false },
        { article: "280.3", shared: true }
      ],
      events: [{ kind: "arrest", label: "Арест", count: 2 }],
      mention_count: 5,
      article_count: 2,
      last_published_at: "2026-09-10T00:00:00Z",
      variants: ["Моора", "Моору"],
      dossier_url: "/ui/investigations/%D0%B0"
    }
  ]
};
const QUERY = {
  q: "",
  article: "",
  role: "figurant",
  verdict: "all",
  region: "",
  rf: "all",
  rf_possible: "all",
  sort: "mentions",
  page: 1
};

beforeEach(() => list.mockReset());

it("shows the people with their marks, articles and events", async () => {
  list.mockReturnValue(ok(PAGE) as never);

  renderPage(<EntitiesPage />);

  expect((await screen.findByRole("link", { name: "Моор Александр" })).getAttribute("href")).toBe("/ui/investigations/%D0%B0");
  for (const mark of ["ИИ", "в перечне", "фигурант дела", "политическое", "Арест: 2", "Моора, Моору"]) {
    expect(screen.getByText(mark)).toBeTruthy();
  }
  expect(screen.getByRole("button", { name: "280.3" }).getAttribute("title")).toBe("общая");
  expect(screen.getByText(/Скрыть тех, кто в перечне РФМ \(4\)/)).toBeTruthy();
  expect(list).toHaveBeenCalledWith({ query: QUERY });
});

it("sends the filters of the address", async () => {
  list.mockReturnValue(ok({ ...PAGE, items: [], total: 0 }) as never);

  renderPage(<EntitiesPage />, { url: "/?q=Моор&article=207.3&role=all&rf=hide&sort=name&page=2" });

  expect(await screen.findByText("Никого не найдено: ослабьте фильтры.")).toBeTruthy();
  expect(list).toHaveBeenCalledWith({
    query: { ...QUERY, q: "Моор", article: "207.3", role: "all", rf: "hide", sort: "name", page: 2 }
  });
  expect(screen.getByText(/по статье УК 207.3/)).toBeTruthy();
});

it("shows the API's own error", async () => {
  list.mockReturnValue(failed(422, [{ msg: "String should match pattern" }]) as never);

  renderPage(<EntitiesPage />);

  expect(await screen.findByText("String should match pattern")).toBeTruthy();
});
