import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { expect, it, vi } from "vitest";

import { App } from "@/App";
import { NAV } from "@/lib/navigation";

vi.mock("@/api/generated", () => ({
  getAboutV1: vi.fn(() => new Promise(() => undefined)),
  getStatusV1: vi.fn(() =>
    Promise.resolve({
      data: {
        articles: 128450,
        people: 13765,
        result: 412,
        queue: { total: 37, pairs: 5, unclear_roles: 10, unclear_verdicts: 12, unnamed: 8, junk_holds: 2 },
        latest_monitoring_status: "completed_with_errors",
        live_operation: { mode: "entities", title: "Сборка сущностей" },
        next_action: "Дождаться окончания шага.",
        balance: { figure: "$1.00", hint: "Куплено $15.00, потрачено $14.00.", low: true, known: true }
      },
      error: undefined,
      response: new Response(null, { status: 200 })
    })
  )
}));

function renderApp(url: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[url]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

it("shows the legacy menu's groups and words", () => {
  renderApp("/about");
  const menu = screen.getByRole("navigation", { name: "Главное меню" });

  for (const group of ["Каждый день", "Данные", "Система"]) {
    expect(within(menu).getByText(group)).toBeTruthy();
  }
  expect(within(menu).getByRole("link", { name: /^Работа/ }).getAttribute("href")).toBe("/work");
  expect(within(menu).getByRole("link", { name: /^Спросить/ }).getAttribute("href")).toBe("/ask");
  // The review stations are opened from «Работа», as in the legacy menu.
  for (const station of ["Пары", "Неясные роли", "Неясная политичность"]) {
    expect(within(menu).queryByRole("link", { name: station })).toBeNull();
  }
});

it("has the legacy menu's items and no other", () => {
  renderApp("/about");
  const menu = screen.getByRole("navigation", { name: "Главное меню" });

  expect(within(menu).getAllByRole("link").map((link) => (link.textContent ?? "").replace(/[\d\s]+$/, ""))).toEqual([
    "Работа",
    "Результаты",
    "Найти человека",
    "Спросить",
    "Все люди",
    "Публикации",
    "Приговоры",
    "Перечень РФМ",
    "База Airtable",
    "Журнал запусков",
    "Логи",
    "Вики",
    "О системе"
  ]);
  // Each with its icon, as there.
  expect(within(menu).getAllByRole("link").every((link) => link.querySelector("svg"))).toBe(true);
});

it("folds what the page is and what to do next under «Как это работает»", async () => {
  renderApp("/wiki");
  const hint = screen.getByRole("button", { name: /Как это работает/ });

  expect(screen.queryByText(/Справочник по проекту/)).toBeNull();
  fireEvent.click(hint);

  expect(screen.getByText(/Справочник по проекту/)).toBeTruthy();
  expect(await screen.findByText("Дождаться окончания шага.")).toBeTruthy();
});

it("shows the balance at the end of the strip, with its hint", async () => {
  renderApp("/about");
  const strip = await screen.findByRole("region", { name: "Показатели" });

  const figure = await within(strip).findByText("$1.00");
  expect(within(strip).getByText("Баланс OpenRouter")).toBeTruthy();
  expect(figure.closest("[title]")?.getAttribute("title")).toBe("Куплено $15.00, потрачено $14.00.");
});

it("sends every item to its React page", () => {
  renderApp("/about");
  const menu = screen.getByRole("navigation", { name: "Главное меню" });

  expect(within(menu).getByRole("link", { name: "О системе" }).getAttribute("href")).toBe("/about");
  expect(within(menu).getByRole("link", { name: "Перечень РФМ" }).getAttribute("href")).toBe("/rfm");
  expect(within(menu).getByRole("link", { name: /^Публикации/ }).getAttribute("href")).toBe("/publications");
  expect(within(menu).getByRole("link", { name: /^Приговоры/ }).getAttribute("href")).toBe("/sentences");
  expect(within(menu).getByRole("link", { name: "Вики" }).getAttribute("href")).toBe("/wiki");
  // Every page is in React now: no item opens the legacy interface.
  expect(within(menu).queryAllByLabelText("старый интерфейс")).toHaveLength(0);
});

it("keeps every item reachable", () => {
  for (const item of NAV.flatMap((group) => group.items)) {
    expect(item.path ?? item.legacy).toBeTruthy();
  }
});

it("says an address it does not know is not a page", () => {
  renderApp("/nowhere-yet");
  expect(screen.getByRole("heading", { name: "Страница не найдена" })).toBeTruthy();
});

it("shows the legacy strip, the menu's counters and the step running now", async () => {
  renderApp("/about");
  const strip = await screen.findByRole("region", { name: "Показатели" });

  expect(within(strip).getByText("Идёт: Сборка сущностей")).toBeTruthy();
  expect(within(strip).getByText("Завершён с ошибками")).toBeTruthy();
  expect(within(strip).getByText(/^128\s450$/)).toBeTruthy();
  const menu = screen.getByRole("navigation", { name: "Главное меню" });
  expect(within(within(menu).getByRole("link", { name: /^Работа/ })).getByText("37")).toBeTruthy();
  expect(within(within(menu).getByRole("link", { name: /^Результаты/ })).getByText("412")).toBeTruthy();
});
