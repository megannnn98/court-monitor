import { screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { getMonitoringStatusV1, listMonitoringFindingsV1, listMonitoringRunsV1 } from "@/api/generated";
import { MonitoringPage } from "@/pages/MonitoringPage";
import { failed, ok, renderPage } from "@/test/render";

vi.mock("@/api/generated", () => ({
  getMonitoringStatusV1: vi.fn(),
  listMonitoringRunsV1: vi.fn(),
  listMonitoringFindingsV1: vi.fn()
}));
const status = vi.mocked(getMonitoringStatusV1);
const runs = vi.mocked(listMonitoringRunsV1);
const findings = vi.mocked(listMonitoringFindingsV1);

const RUN = {
  id: 501,
  scope: "source",
  source: "ovd",
  trigger_type: "schedule",
  status: "completed_with_errors",
  started_at: "2026-10-07T06:00:00Z",
  heartbeat_at: "2026-10-07T06:10:00Z",
  finished_at: "2026-10-07T06:10:00Z",
  duration_seconds: 600,
  documents_discovered: 40,
  documents_ingested: 38,
  documents_skipped: 1,
  documents_failed: 1,
  articles_extracted: 37,
  events_created: 5,
  persons_created: 2,
  persons_linked: 3,
  person_reviews_created: 0,
  classifications_created: 2,
  rf_matches_created: 2,
  semantic_entities_indexed: 0,
  findings_created: 1,
  error_count: 1,
  rf_snapshot_id: 12,
  error_message: "timeout"
};

beforeEach(() => {
  status.mockReset();
  runs.mockReset();
  findings.mockReset();
  findings.mockReturnValue(ok([]) as never);
});

it("shows the summary, the sources and the runs", async () => {
  status.mockReturnValue(
    ok({
      running: [],
      latest_runs: [RUN],
      active_findings: 4,
      sources: [
        {
          source_name: "ovd",
          last_successful_run_id: 500,
          last_successful_run_at: "2026-10-06T06:00:00Z",
          last_discovered_at: "2026-10-07T06:00:00Z",
          last_discovered_count: 40,
          last_external_marker: null,
          updated_at: "2026-10-07T06:10:00Z"
        }
      ]
    }) as never
  );
  runs.mockReturnValue(ok([RUN]) as never);

  renderPage(<MonitoringPage />);

  expect(await screen.findByText("Завершён с ошибками")).toBeTruthy();
  expect(screen.getByText("4")).toBeTruthy();
  expect(screen.getByRole("button", { name: "ovd" })).toBeTruthy();
  expect(screen.getByText("37")).toBeTruthy();
  expect(runs).toHaveBeenCalledWith({ query: { limit: 50, offset: 0, status: null, source: null } });
});

it("filters the runs by the source and the status in the address", async () => {
  status.mockReturnValue(ok({ running: [], latest_runs: [], sources: [], active_findings: 0 }) as never);
  runs.mockReturnValue(ok([]) as never);

  renderPage(<MonitoringPage />, { url: "/?source=ovd&status=failed" });

  expect(await screen.findByText("Запусков мониторинга нет.")).toBeTruthy();
  expect(screen.getByText("Состояния источников нет.")).toBeTruthy();
  expect(runs).toHaveBeenCalledWith({ query: { limit: 50, offset: 0, status: "failed", source: "ovd" } });
});

it("shows the API's own error", async () => {
  status.mockReturnValue(failed(500, "monitoring tables missing") as never);
  runs.mockReturnValue(failed(500, "monitoring tables missing") as never);

  renderPage(<MonitoringPage />);

  await waitFor(() => expect(screen.getAllByText("monitoring tables missing")).toHaveLength(2));
});
