import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { listEntitiesV1 } from "@/api/generated";
import { EntitiesPage } from "@/pages/EntitiesPage";
import { dossierPath } from "@/lib/navigation";
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
  verdicts: [
    { value: "all", label: "Любой вердикт" },
    { value: "political", label: "Политические", count: 7 }
  ],
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

  expect((await screen.findByRole("link", { name: "Моор Александр" })).getAttribute("href")).toBe(dossierPath("александр моор"));
  const table = screen.getByRole("table");
  for (const mark of ["в перечне", "политическое", "Арест 2"]) {
    expect(within(table).getByText(mark)).toBeTruthy();
  }
  expect(within(table).getByText("Москва · 2 написания").getAttribute("title")).toBe("Как писали: Моора, Моору\nИмя: ИИ");
  // The usual role is not repeated on every row.
  expect(within(table).queryByText("фигурант дела")).toBeNull();
  expect(screen.getByRole("button", { name: "280.3" }).getAttribute("title")).toMatch(/^общая/);
  expect(screen.getByText(/Скрыть, кто в перечне \(4\)/)).toBeTruthy();
  expect(list).toHaveBeenCalledWith({ query: QUERY });
});

it("filters from the column on the left and shows the choice as chips", async () => {
  list.mockReturnValue(ok(PAGE) as never);

  renderPage(<EntitiesPage />);
  const filters = await screen.findByRole("complementary", { name: "Фильтры" });
  fireEvent.click(within(filters).getByRole("button", { name: "Политические 7" }));
  await waitFor(() => expect(list).toHaveBeenLastCalledWith({ query: { ...QUERY, verdict: "political" } }));
  fireEvent.click(within(filters).getByRole("button", { name: "Москва" }));
  await waitFor(() => expect(list).toHaveBeenLastCalledWith({ query: { ...QUERY, verdict: "political", region: "Москва" } }));

  // A chip names the choice in words and takes it back.
  fireEvent.click(screen.getByRole("button", { name: "Убрать фильтр: Политические" }));
  await waitFor(() => expect(list).toHaveBeenLastCalledWith({ query: { ...QUERY, region: "Москва" } }));
});

it("says the list holds political and common cases alike, until one kind is chosen", async () => {
  list.mockReturnValue(ok(PAGE) as never);
  renderPage(<EntitiesPage />);

  expect(await screen.findByText(/и политические дела, и обычные уголовные/)).toBeTruthy();
  expect(screen.getByRole("link", { name: "«Результатах»" }).getAttribute("href")).toBe("/political");
  fireEvent.click(screen.getByRole("button", { name: "фильтром «Вердикт: политические»" }));

  await waitFor(() => expect(list).toHaveBeenLastCalledWith({ query: expect.objectContaining({ verdict: "political" }) }));
  expect(screen.queryByText(/и политические дела, и обычные уголовные/)).toBeNull();
});

it("looks for a region among the regions", async () => {
  list.mockReturnValue(ok({ ...PAGE, regions: ["Москва", "Якутия"] }) as never);

  renderPage(<EntitiesPage />);
  const regions = await screen.findByRole("region", { name: "Регион" });
  fireEvent.change(within(regions).getByLabelText("Найти регион"), { target: { value: "як" } });

  expect(within(regions).getByRole("button", { name: "Якутия" })).toBeTruthy();
  expect(within(regions).queryByRole("button", { name: "Москва" })).toBeNull();
});

it("sends the filters of the address", async () => {
  list.mockReturnValue(ok({ ...PAGE, items: [], total: 0 }) as never);

  renderPage(<EntitiesPage />, { url: "/?q=Моор&article=207.3&role=all&rf=hide&sort=name&page=2" });

  expect(await screen.findByText("Никого не найдено: ослабьте фильтры.")).toBeTruthy();
  expect(list).toHaveBeenCalledWith({
    query: { ...QUERY, q: "Моор", article: "207.3", role: "all", rf: "hide", sort: "name", page: 2 }
  });
  for (const chip of ["имя: Моор", "статья 207.3", "Все роли", "без тех, кто в перечне"]) {
    expect(screen.getByRole("button", { name: `Убрать фильтр: ${chip}` })).toBeTruthy();
  }
});

it("shows the API's own error", async () => {
  list.mockReturnValue(failed(422, [{ msg: "String should match pattern" }]) as never);

  renderPage(<EntitiesPage />);

  expect(await screen.findByText("String should match pattern")).toBeTruthy();
});

it("on a narrow screen folds the filters under one line", async () => {
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: false, media: query, addEventListener: () => undefined, removeEventListener: () => undefined }));
  try {
    list.mockReturnValue(ok(PAGE) as never);
    renderPage(<EntitiesPage />);

    const fold = (await screen.findByText("Фильтры: роль, вердикт, перечень, регион")).closest("details");
    // The search stays in sight; the choices are inside the fold.
    expect(fold?.open).toBe(false);
    expect(fold?.querySelector("h2")?.textContent).toBe("Роль");
    expect(screen.getByRole("search").closest("details")).toBeNull();
  } finally {
    vi.unstubAllGlobals();
  }
});
