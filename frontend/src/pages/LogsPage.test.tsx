import { screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { getOperationRunV1, listOperationRunsV1 } from "@/api/generated";
import { LogsPage } from "@/pages/LogsPage";
import { failed, ok, renderPage } from "@/test/render";

vi.mock("@/api/generated", () => ({ listOperationRunsV1: vi.fn(), getOperationRunV1: vi.fn() }));
const list = vi.mocked(listOperationRunsV1);
const one = vi.mocked(getOperationRunV1);

function run(id: number, status: string, stdout: string) {
  return {
    id,
    operation: "monitor",
    title: `Шаг ${id}`,
    parameters: {},
    status,
    created_at: "2026-10-07T08:00:00Z",
    started_at: "2026-10-07T08:00:05Z",
    finished_at: null,
    duration_seconds: 10,
    command: ["court-monitor", "monitor"],
    return_code: null,
    stdout,
    stderr: "",
    error: null
  };
}

beforeEach(() => {
  list.mockReset();
  one.mockReset();
});

it("opens on the latest run's output", async () => {
  list.mockReturnValue(ok([run(9, "running", "свежий"), run(8, "succeeded", "старый")]) as never);
  one.mockImplementation((options) => ok(options.path.run_id === 9 ? run(9, "running", "свежий вывод") : run(8, "succeeded", "старый вывод")) as never);

  renderPage(<LogsPage />);

  expect(await screen.findByText("свежий вывод")).toBeTruthy();
  expect(one).toHaveBeenCalledWith({ path: { run_id: 9 } });
  expect(screen.getByRole("button", { name: "#8" })).toBeTruthy();
});

it("opens the run of the address", async () => {
  list.mockReturnValue(ok([run(9, "running", ""), run(8, "succeeded", "")]) as never);
  one.mockReturnValue(ok(run(8, "succeeded", "старый вывод")) as never);

  renderPage(<LogsPage />, { url: "/?run=8" });

  expect(await screen.findByText("старый вывод")).toBeTruthy();
  expect(one).toHaveBeenCalledWith({ path: { run_id: 8 } });
});

it("says when nothing has run and shows the API's error", async () => {
  list.mockReturnValue(ok([]) as never);
  const { unmount } = renderPage(<LogsPage />);
  expect(await screen.findByText("Запусков ещё не было.")).toBeTruthy();
  unmount();

  list.mockReturnValue(failed(503, "registry unavailable") as never);
  renderPage(<LogsPage />);
  expect(await screen.findByText("registry unavailable")).toBeTruthy();
});
