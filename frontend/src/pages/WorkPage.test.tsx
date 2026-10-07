import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { getCycleV1, startCycleV1, stopCycleRunV1 } from "@/api/generated";
import { WorkPage } from "@/pages/WorkPage";
import { failed, ok, renderPage } from "@/test/render";

vi.mock("@/api/generated", () => ({ getCycleV1: vi.fn(), startCycleV1: vi.fn(), stopCycleRunV1: vi.fn() }));
const cycle = vi.mocked(getCycleV1);
const start = vi.mocked(startCycleV1);
const stop = vi.mocked(stopCycleRunV1);

const STEPS = [
  { stage: "load", number: 1, label: "Публикации загружены", status: "ready" },
  { stage: "purge", number: 2, label: "Публикации проверены", status: "waiting" }
];
const IDLE = {
  attention: { key: "pairs", title: "Спорные пары", count: 4, href: "/ui/pairs", description: "Решите, один ли это человек." },
  tasks: [{ key: "unnamed", title: "Безымянные", count: 2, href: "/ui/unnamed", description: "" }],
  steps: STEPS,
  current_stage: "load",
  live: null,
  latest_run_id: 41,
  chain_span: "шаги 1–5 подряд",
  chain_question: "Выполнить шаги 1–5 подряд? Шаг 2. Удалить … Это необратимо.",
  chain_note: "",
  chain_stopped: ""
};

beforeEach(() => {
  for (const mock of [cycle, start, stop]) {
    mock.mockReset();
  }
});

it("sends a moved review to its React page and the rest to the legacy one", async () => {
  cycle.mockReturnValue(ok(IDLE) as never);

  renderPage(<WorkPage />);

  expect((await screen.findByRole("link", { name: "Начать проверку" })).getAttribute("href")).toBe("/review/pairs");
  expect(screen.getByRole("link", { name: /Безымянные/ }).getAttribute("href")).toBe("/ui/unnamed");
  expect(screen.getByText("Публикации загружены")).toBeTruthy();
});

it("asks before «Сделать всё» and names the latest run it was read for", async () => {
  cycle.mockReturnValue(ok(IDLE) as never);
  start.mockReturnValue(ok({ run_id: 42, stage: "load" }) as never);

  renderPage(<WorkPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Сделать всё" }));

  expect(await screen.findByText(/Это необратимо/)).toBeTruthy();
  expect(start).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Запустить" }));
  await waitFor(() => expect(start).toHaveBeenCalledWith({ body: { after: 41 } }));
});

it("stops a live run and shows a refusal", async () => {
  cycle.mockReturnValue(
    ok({ ...IDLE, attention: null, live: { run_id: 42, title: "Подгрузить статьи" }, chain_note: "Идёт «Сделать всё»" }) as never
  );
  stop.mockReturnValue(failed(404, "Запуск не найден") as never);

  renderPage(<WorkPage />);
  expect(await screen.findByText("Идёт автоматическая обработка")).toBeTruthy();
  expect(screen.getByRole("link", { name: /Ход запуска #42/ }).getAttribute("href")).toBe("/logs?run=42");
  fireEvent.click(screen.getByRole("button", { name: "Остановить" }));
  fireEvent.click(await screen.findByRole("button", { name: "Остановить" }));

  await waitFor(() => expect(stop).toHaveBeenCalledWith({ path: { run_id: 42 } }));
  expect(await screen.findByText("Запуск не найден")).toBeTruthy();
});
