import { screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { getOperationRunV1, listOperationRunsV1 } from "@/api/generated";
import { RunsPage } from "@/pages/RunsPage";
import { failed, ok, renderPage } from "@/test/render";

vi.mock("@/api/generated", () => ({ listOperationRunsV1: vi.fn(), getOperationRunV1: vi.fn() }));
const list = vi.mocked(listOperationRunsV1);
const one = vi.mocked(getOperationRunV1);

const RUN = {
  id: 31,
  operation: "pipeline",
  title: "Сборка сущностей",
  parameters: {},
  status: "running",
  created_at: "2026-10-07T08:00:00Z",
  started_at: "2026-10-07T08:00:05Z",
  finished_at: null,
  duration_seconds: 125,
  command: ["court-monitor", "entities"],
  return_code: null,
  stdout: "читаю статьи",
  stderr: "",
  error: null
};

beforeEach(() => {
  list.mockReset();
  one.mockReset();
});

it("lists the runs with their state and offers no start or stop", async () => {
  list.mockReturnValue(ok([RUN, { ...RUN, id: 30, status: "failed", title: "Загрузка статей", duration_seconds: 7 }]) as never);

  renderPage(<RunsPage />);

  expect(await screen.findByText("Сборка сущностей")).toBeTruthy();
  expect(screen.getByText("Выполняется")).toBeTruthy();
  expect(screen.getByText("Завершено с ошибками")).toBeTruthy();
  expect(screen.getByText("2 мин 5 с")).toBeTruthy();
  expect(screen.queryByRole("button", { name: /Запустить|Остановить/ })).toBeNull();
});

it("shows the chosen run's output", async () => {
  list.mockReturnValue(ok([RUN]) as never);
  one.mockReturnValue(ok(RUN) as never);

  renderPage(<RunsPage />, { url: "/?run=31" });

  expect(await screen.findByText("читаю статьи")).toBeTruthy();
  expect(screen.getByText("court-monitor entities")).toBeTruthy();
  expect(one).toHaveBeenCalledWith({ path: { run_id: 31 } });
});

it("says when nothing has run yet", async () => {
  list.mockReturnValue(ok([]) as never);

  renderPage(<RunsPage />);

  expect(await screen.findByText("Запусков ещё не было.")).toBeTruthy();
});

it("shows the API's own error", async () => {
  list.mockReturnValue(failed(503, "registry unavailable") as never);

  renderPage(<RunsPage />);

  expect(await screen.findByText("registry unavailable")).toBeTruthy();
});
