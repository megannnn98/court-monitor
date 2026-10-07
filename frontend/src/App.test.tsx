import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { expect, it, vi } from "vitest";

import { App } from "@/App";
import { NAV } from "@/lib/navigation";

vi.mock("@/api/generated", () => ({
  getAboutV1: vi.fn(() => new Promise(() => undefined))
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
  expect(within(menu).getByRole("link", { name: /^Работа/ }).getAttribute("href")).toBe("/ui/cycle");
});

it("sends a moved item to React and the rest to the legacy page, marked", () => {
  renderApp("/about");
  const menu = screen.getByRole("navigation", { name: "Главное меню" });

  expect(within(menu).getByRole("link", { name: "О системе" }).getAttribute("href")).toBe("/about");
  expect(within(menu).getByRole("link", { name: "Перечень РФМ" }).getAttribute("href")).toBe("/rfm");
  const legacy = within(menu).getByRole("link", { name: /Публикации/ });
  expect(legacy.getAttribute("href")).toBe("/ui/publications");
  expect(within(legacy).getByLabelText("старый интерфейс")).toBeTruthy();
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
