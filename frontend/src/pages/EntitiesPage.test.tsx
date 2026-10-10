import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { listEntitiesV1, listRemovalsV1, setRemovalV1 } from "@/api/generated";
import { EntitiesPage } from "@/pages/EntitiesPage";
import { dossierPath } from "@/lib/navigation";
import { failed, ok, renderPage } from "@/test/render";

vi.mock("@/api/generated", () => ({ listEntitiesV1: vi.fn(), listRemovalsV1: vi.fn(), setRemovalV1: vi.fn() }));
const list = vi.mocked(listEntitiesV1);
const removals = vi.mocked(listRemovalsV1);
const removal = vi.mocked(setRemovalV1);

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

beforeEach(() => {
  list.mockReset();
  removal.mockReset();
  removals.mockReset();
  removals.mockReturnValue(ok({ items: [] }) as never);
});

it("shows the people with their marks and articles, and the first one in the panel", async () => {
  list.mockReturnValue(ok(PAGE) as never);

  renderPage(<EntitiesPage />);

  const table = await screen.findByRole("table");
  expect(within(table).getByRole("link", { name: "Моор Александр" }).getAttribute("href")).toBe(dossierPath("александр моор"));
  for (const mark of ["в перечне", "политическое", "Москва"]) {
    expect(within(table).getByText(mark)).toBeTruthy();
  }
  // The usual role is not repeated on every row.
  expect(within(table).queryByText("фигурант дела")).toBeNull();
  expect(within(table).getByRole("button", { name: "280.3" }).getAttribute("title")).toMatch(/^общая/);
  // What the table leaves out is beside it.
  const panel = screen.getByRole("complementary", { name: "Подробно: Моор Александр" });
  for (const text of ["Моора, Моору", "Имя: ИИ", "Арест 2", "Упоминаний: 5 · публикаций: 2"]) {
    expect(within(panel).getByText(text)).toBeTruthy();
  }
  expect(within(panel).getByRole("link", { name: "Открыть досье" }).getAttribute("href")).toBe(dossierPath("александр моор"));
  expect(screen.getByText(/Скрыть, кто в перечне \(4\)/)).toBeTruthy();
  expect(list).toHaveBeenCalledWith({ query: QUERY });
});

it("shows in the panel the person whose row is clicked", async () => {
  const other = { ...PAGE.items[0], id: 4, key: "иван иванов", name: "Иванов Иван", variants: ["Иванова"] };
  list.mockReturnValue(ok({ ...PAGE, items: [PAGE.items[0], other], total: 2 }) as never);

  renderPage(<EntitiesPage />);
  const rows = await screen.findAllByRole("row");
  fireEvent.click(within(rows[2]).getAllByRole("cell")[3]);

  expect(screen.getByRole("complementary", { name: "Подробно: Иванов Иван" })).toBeTruthy();
  expect(rows[2].getAttribute("aria-selected")).toBe("true");
});

it("filters by the role's tab, the verdict's button and an article, and shows the choice as chips", async () => {
  list.mockReturnValue(ok(PAGE) as never);

  renderPage(<EntitiesPage />);
  const roles = await screen.findByRole("navigation", { name: "Роль" });
  fireEvent.click(within(roles).getByRole("button", { name: "Все роли" }));
  await waitFor(() => expect(list).toHaveBeenLastCalledWith({ query: { ...QUERY, role: "all" } }));
  fireEvent.click(within(screen.getByRole("group", { name: "Вердикт" })).getByRole("button", { name: "Политические 7" }));
  await waitFor(() => expect(list).toHaveBeenLastCalledWith({ query: { ...QUERY, role: "all", verdict: "political" } }));
  fireEvent.click(within(screen.getByRole("table")).getByRole("button", { name: "205.2" }));
  await waitFor(() => expect(list).toHaveBeenLastCalledWith({ query: { ...QUERY, role: "all", verdict: "political", article: "205.2" } }));

  // A chip names the choice in words and takes it back.
  fireEvent.click(screen.getByRole("button", { name: "Убрать фильтр: Политические" }));
  await waitFor(() => expect(list).toHaveBeenLastCalledWith({ query: { ...QUERY, role: "all", article: "205.2" } }));
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

it("sends the filters of the address", async () => {
  list.mockReturnValue(ok({ ...PAGE, items: [], total: 0 }) as never);

  renderPage(<EntitiesPage />, { url: "/?q=Моор&article=207.3&role=all&region=Москва&rf=hide&sort=name&page=2" });

  expect(await screen.findByText("Никого не найдено: ослабьте фильтры.")).toBeTruthy();
  expect(list).toHaveBeenCalledWith({
    query: { ...QUERY, q: "Моор", article: "207.3", role: "all", region: "Москва", rf: "hide", sort: "name", page: 2 }
  });
  for (const chip of ["имя: Моор", "статья 207.3", "Москва", "Все роли", "без тех, кто в перечне"]) {
    expect(screen.getByRole("button", { name: `Убрать фильтр: ${chip}` })).toBeTruthy();
  }
});

it("shows the API's own error", async () => {
  list.mockReturnValue(failed(422, [{ msg: "String should match pattern" }]) as never);

  renderPage(<EntitiesPage />);

  expect(await screen.findByText("String should match pattern")).toBeTruthy();
});

it("on a narrow screen folds the rarer filters and opens the person tapped over the table", async () => {
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: false, media: query, addEventListener: () => undefined, removeEventListener: () => undefined }));
  try {
    list.mockReturnValue(ok(PAGE) as never);
    renderPage(<EntitiesPage />);

    const fold = (await screen.findByText("Ещё фильтры: регион, перечень, сортировка")).closest("details");
    // The search stays in sight; the rarer choices are inside the fold.
    expect(fold?.open).toBe(false);
    expect(fold?.textContent).toContain("Скрыть, кто в перечне");
    expect(screen.getByRole("search").closest("details")).toBeNull();

    expect(screen.queryByRole("complementary", { name: /Подробно/ })).toBeNull();
    fireEvent.click(within(screen.getAllByRole("row")[1]).getAllByRole("cell")[3]);
    const sheet = await screen.findByRole("dialog");
    expect(within(sheet).getByRole("complementary", { name: "Подробно: Моор Александр" })).toBeTruthy();
  } finally {
    vi.unstubAllGlobals();
  }
});

it("lists the people removed by hand and takes a removal back", async () => {
  list.mockReturnValue(ok(PAGE) as never);
  const putin = { key: "дмитрий путин", name: "Путин Дмитрий", removed: true };
  removals.mockReturnValueOnce(ok({ items: [putin] }) as never);
  removal.mockReturnValue(ok({ ...putin, removed: false }) as never);

  renderPage(<EntitiesPage />);
  const fold = (await screen.findByText("Удалённые вручную (1)")).closest("details") as HTMLElement;
  fireEvent.click(within(fold).getByRole("button", { name: "Вернуть" }));

  // Back only at the next rebuild: said, so the empty list does not look like a failure.
  expect((await screen.findByRole("status")).textContent).toBe(
    "«Путин Дмитрий» возвращён: появится в списке после следующего шага 3 «Собрать сущности»."
  );
  expect(removal).toHaveBeenCalledWith({ body: { key: "дмитрий путин", removed: false } });
  await waitFor(() => expect(screen.getByText("Удалённые вручную (0)")).toBeTruthy());
});

it("shows no list of the removed while nobody is removed", async () => {
  list.mockReturnValue(ok(PAGE) as never);
  renderPage(<EntitiesPage />);

  await screen.findByRole("table");
  await waitFor(() => expect(removals).toHaveBeenCalled());
  expect(screen.queryByText(/Удалённые вручную/)).toBeNull();
});
