import { screen, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { getOverviewV1 } from "@/api/generated";
import { OverviewPage } from "@/pages/OverviewPage";
import { ok, renderPage } from "@/test/render";

vi.mock("@/api/generated", () => ({ getOverviewV1: vi.fn() }));
const get = vi.mocked(getOverviewV1);

beforeEach(() => get.mockReset());

it("shows the new cases, the unnamed and what waits for a decision", async () => {
  get.mockReturnValue(
    ok({
      source_errors: 2,
      new_cases: 3,
      latest_new_cases: [{ key: "анна смирнова", name: "Смирнова Анна", published_at: "2026-10-01T00:00:00Z", reason: "возбуждено дело" }],
      sentences: 0,
      latest_sentences: [],
      unnamed: 1,
      latest_unnamed: [{ key: "k1", quote: "задержан 17-летний житель", published_at: null, facts: "17 лет · мужчина", found: "вероятных в перечне: 1" }],
      pairs: 4,
      unclear_roles: 0,
      unclear_verdicts: 1
    }) as never
  );
  renderPage(<OverviewPage />);

  const news = await screen.findByRole("region", { name: "Новые дела" });
  expect(within(news).getByRole("link", { name: "Все: 3 →" }).getAttribute("href")).toBe("/political?queue=new_case");
  expect(within(screen.getByRole("region", { name: "Приговоры" })).getByText("Приговоров нет.")).toBeTruthy();
  expect(screen.getByRole("link", { name: "«задержан 17-летний житель»" }).getAttribute("href")).toBe("/unnamed#u-k1");
  expect(screen.getByRole("link", { name: "спорных совпадений — 4" }).getAttribute("href")).toBe("/review/pairs");
  expect(screen.getByText(/Источников с ошибками загрузки за неделю: 2/)).toBeTruthy();
});
