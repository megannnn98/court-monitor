import { screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { listPublicationsV1 } from "@/api/generated";
import { PublicationsPage } from "@/pages/PublicationsPage";
import { dossierPath } from "@/lib/navigation";
import { failed, ok, renderPage } from "@/test/render";

vi.mock("@/api/generated", () => ({ listPublicationsV1: vi.fn() }));
const list = vi.mocked(listPublicationsV1);

const PAGE = {
  total: 1,
  page: 1,
  page_size: 50,
  sources: [{ id: 2, name: "ОВД-Инфо", count: 120 }],
  items: [
    {
      id: 77,
      title: "Приговор Моору",
      published_at: "2026-09-10T00:00:00Z",
      source: "ОВД-Инфо",
      url: "https://ovd.example/1",
      people: [{ key: "александр моор", name: "Моор Александр", dossier_url: "/ui/investigations/x" }],
      more_people: 3,
      events: [
        { kind: "sentence", label: "Приговор", count: 1 },
        { kind: "arrest", label: "Арест", count: 2 }
      ]
    }
  ]
};

beforeEach(() => list.mockReset());

it("shows each publication with its people and events", async () => {
  list.mockReturnValue(ok(PAGE) as never);

  renderPage(<PublicationsPage />);

  expect((await screen.findByRole("link", { name: "Приговор Моору" })).getAttribute("href")).toBe("/articles/77");
  expect(screen.getByRole("link", { name: "Моор Александр" }).getAttribute("href")).toBe(dossierPath("александр моор"));
  expect(screen.getByText("и ещё 3")).toBeTruthy();
  expect(screen.getByText("Приговор")).toBeTruthy();
  expect(screen.getByText("Арест: 2")).toBeTruthy();
  expect(list).toHaveBeenCalledWith({ query: { q: "", source: 0, page: 1 } });
});

it("sends the search, the source and the page of the address", async () => {
  list.mockReturnValue(ok({ ...PAGE, items: [], total: 0 }) as never);

  renderPage(<PublicationsPage />, { url: "/?q=пикет&source=2&page=3" });

  expect(await screen.findByText("Ничего не найдено.")).toBeTruthy();
  expect(list).toHaveBeenCalledWith({ query: { q: "пикет", source: 2, page: 3 } });
});

it("shows the API's own error", async () => {
  list.mockReturnValue(failed(500, "search failed") as never);

  renderPage(<PublicationsPage />);

  expect(await screen.findByText("search failed")).toBeTruthy();
});
