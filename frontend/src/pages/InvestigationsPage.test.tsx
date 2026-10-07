import { screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { searchInvestigationsV1 } from "@/api/generated";
import { dossierPath } from "@/lib/navigation";
import { InvestigationsPage } from "@/pages/InvestigationsPage";
import { failed, ok, renderPage } from "@/test/render";

vi.mock("@/api/generated", () => ({ searchInvestigationsV1: vi.fn() }));
const search = vi.mocked(searchInvestigationsV1);

beforeEach(() => search.mockReset());

it("opens on the latest political cases, each linked to its dossier", async () => {
  search.mockReturnValue(
    ok({
      heading: "Свежие политические дела",
      items: [{ key: "александр моор", name: "Моор Александр", role_label: "фигурант дела", article_count: 3, last_published_at: "2026-09-10T00:00:00Z" }]
    }) as never
  );

  renderPage(<InvestigationsPage />);

  expect(await screen.findByRole("heading", { name: "Свежие политические дела" })).toBeTruthy();
  expect(screen.getByRole("link", { name: "Моор Александр" }).getAttribute("href")).toBe(dossierPath("александр моор"));
  expect(screen.getByText("фигурант дела")).toBeTruthy();
  expect(search).toHaveBeenCalledWith({ query: { q: "" } });
});

it("searches by the name in the address and says when nobody is found", async () => {
  search.mockReturnValue(ok({ heading: "Найдено по «Никто»", items: [] }) as never);

  renderPage(<InvestigationsPage />, { url: "/?q=Никто" });

  expect(await screen.findByText("Никого не найдено.")).toBeTruthy();
  expect(search).toHaveBeenCalledWith({ query: { q: "Никто" } });
});

it("shows the API's own error", async () => {
  search.mockReturnValue(failed(500, "search unavailable") as never);

  renderPage(<InvestigationsPage />);

  expect(await screen.findByText("search unavailable")).toBeTruthy();
});
