import { screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { getDossierV1 } from "@/api/generated";
import { dossierPath } from "@/lib/navigation";
import { DossierPage } from "@/pages/DossierPage";
import { failed, ok, renderPage } from "@/test/render";

vi.mock("@/api/generated", () => ({ getDossierV1: vi.fn() }));
const dossier = vi.mocked(getDossierV1);

const QUOTE = { article_id: 77, title: "Арест Моора", source: "ОВД-Инфо", published_at: "2026-09-01T09:00:00Z", quote: "Суд арестовал Александра Моора.", start: 14, end: 30, text_start: 100, text_end: 116 };
const DOSSIER = {
  key: "александр моор",
  name: "Моор Александр",
  role: "figurant",
  role_label: "фигурант дела",
  role_method_label: "ответ модели по цитатам",
  role_reason: "Арестован по уголовному делу.",
  role_quote: null,
  verdict: "political",
  verdict_label: "политическое",
  verdict_method_label: "модель по цитатам из публикаций",
  verdict_reason: "Преследование за антивоенные посты.",
  verdict_quote: "арестовали за антивоенные посты",
  verdict_source_article_id: 77,
  rf_label: "возможно в перечне (тёзка без отчества)",
  rf_maybe: true,
  rf_entries: [{ level: "name", level_label: "имя и фамилия", text: "МООР АЛЕКСАНДР ИВАНОВИЧ, 01.02.1980 г.р." }],
  rf_expected: null,
  snapshot_date: "2026-10-01T00:00:00Z",
  inclusion_attribution: "Даты включения — по копии ОВД-Инфо.",
  disputes: [],
  variants: [{ form: "Моора", count: 2 }],
  regions: ["Москва"],
  article_count: 2,
  mention_count: 3,
  first_published_at: "2026-09-01T09:00:00Z",
  last_published_at: "2026-09-10T12:00:00Z",
  events: [{ kind: "arrest", label: "Арест", count: 1 }],
  name_source_label: "по правилам склейки",
  gender_label: "мужчина",
  news: null,
  known: { loaded: true, not_in_base: true, label: null, names: [], more: 0 },
  warnings: ["Совпадение с перечнем только по имени и фамилии: может быть тёзка."],
  charges: [{ article: "205.2", parts: ["2"], shared: false, political: true, publications: 1 }],
  timeline: [
    {
      event_type: "arrest",
      label: "Арест",
      day: "2026-09-01T09:00:00Z",
      dated: true,
      confidence: 0.8,
      extractor_label: "правилами",
      articles: ["205.2"],
      orgs: [{ name: "Ленинский суд", role_label: "суд" }],
      sources: [QUOTE]
    }
  ],
  timeline_capped: false,
  timeline_limit: 200,
  publications: [
    {
      ...QUOTE,
      url: "https://ovd.example/1",
      identification: null,
      events: ["Арест"],
      articles: ["205.2"],
      others: [{ key: "иван иванов", name: "Иванов Иван", dossier_url: "/ui/investigations/x" }]
    }
  ],
  related: [{ key: "иван иванов", name: "Иванов Иван", shared: 1 }],
  graph_url: "/api/investigations/x/graph"
};

beforeEach(() => dossier.mockReset());

it("shows the person, the decisions, the charges, the timeline and the evidence", async () => {
  dossier.mockReturnValue(ok(DOSSIER) as never);

  const { container } = renderPage(<DossierPage />, { path: "/investigations/:personKey", url: `/investigations/${encodeURIComponent("александр моор")}` });

  expect(await screen.findByRole("heading", { name: "Моор Александр", level: 1 })).toBeTruthy();
  expect(dossier).toHaveBeenCalledWith({ path: { key: "александр моор" } });
  for (const text of ["Преследование за антивоенные посты.", "нет в базе", "Совпадение с перечнем только по имени и фамилии: может быть тёзка.", "Ленинский суд (суд)"]) {
    expect(screen.getByText(text)).toBeTruthy();
  }
  expect(screen.getByRole("link", { name: "исходная публикация" }).getAttribute("href")).toBe("/articles/77");
  expect(screen.getAllByRole("link", { name: "Арест Моора" })[0].getAttribute("href")).toBe("/articles/77?start=100&end=116");
  expect(container.querySelector("mark")?.textContent).toBe("Александра Моора");
  expect(screen.getAllByRole("link", { name: "Иванов Иван" })[0].getAttribute("href")).toBe(dossierPath("иван иванов"));
  expect(screen.getByRole("link", { name: "Граф событий" }).getAttribute("href")).toContain("/ui/investigations/");
});

it("says what the system has not decided", async () => {
  dossier.mockReturnValue(
    ok({
      ...DOSSIER,
      role: null,
      role_label: null,
      verdict: null,
      verdict_label: null,
      charges: [],
      timeline: [],
      publications: [],
      related: [],
      known: { loaded: false, not_in_base: false, label: null, names: [], more: 0 }
    }) as never
  );

  renderPage(<DossierPage />, { path: "/investigations/:personKey", url: "/investigations/x" });

  expect(await screen.findByText("Шаг 5 ещё не определял роль.")).toBeTruthy();
  expect(screen.getByText("Нет событий, где этот человек назван участником.")).toBeTruthy();
  expect(screen.getByText("Публикаций нет.")).toBeTruthy();
  expect(screen.queryByText("База Airtable")).toBeNull();
});

it("shows the API's own error", async () => {
  dossier.mockReturnValue(failed(404, "Человек не найден") as never);

  renderPage(<DossierPage />, { path: "/investigations/:personKey", url: "/investigations/nobody" });

  expect(await screen.findByText("Человек не найден")).toBeTruthy();
});
