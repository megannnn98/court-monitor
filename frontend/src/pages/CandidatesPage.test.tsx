import { screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { candidateTableV1, listRosfinmonitoringSnapshotsV1 } from "@/api/generated";
import { CandidatesPage } from "@/pages/CandidatesPage";
import { failed, ok, renderPage } from "@/test/render";

vi.mock("@/api/generated", () => ({ candidateTableV1: vi.fn(), listRosfinmonitoringSnapshotsV1: vi.fn() }));
const table = vi.mocked(candidateTableV1);
const snapshots = vi.mocked(listRosfinmonitoringSnapshotsV1);

const SNAPSHOTS = [{ id: 12, snapshot_date: "2026-10-01", source_url: "https://fedsfm.ru", content_hash: "h", entry_count: 23000 }];
const ROW = {
  person_id: 5,
  name: "Смирнова Анна",
  news_day: "2026-09-30",
  category: "Арест",
  persecution_confidence: 0.91,
  event_count: 3,
  rosfinmonitoring_status: "not_matched",
  reasons: ["antiwar_speech", "article_207_3"]
};
const TABLE = { snapshot_id: 12, period_start: "2026-08-23", total: 7, items: [ROW] };
const DEFAULT_QUERY = { min_confidence: 0.7, limit: 100, include_administrative: false, criminal_only: false, event_date_filter: true };

beforeEach(() => {
  table.mockReset();
  snapshots.mockReset();
  snapshots.mockReturnValue(ok(SNAPSHOTS) as never);
});

it("shows the legacy table's rows and links the file of the same people", async () => {
  table.mockReturnValue(ok(TABLE) as never);

  renderPage(<CandidatesPage />);

  expect((await screen.findByRole("link", { name: "Смирнова Анна" })).getAttribute("href")).toBe("/persons/5");
  expect(screen.getByText("Арест")).toBeTruthy();
  expect(screen.getByText("30.09.2026")).toBeTruthy();
  expect(screen.getByText(/Найдено: 7, показано: 1/)).toBeTruthy();
  // No snapshot and no period asked: the server chooses, the file gets what it chose.
  expect(table).toHaveBeenCalledWith({ query: DEFAULT_QUERY });
  expect(screen.getByRole("link", { name: "Скачать Excel" }).getAttribute("href")).toBe(
    "/ui/candidates/export.xlsx?snapshot_id=12&min_confidence=0.7&date_from=2026-08-23"
  );
});

it("sends the filters of the address, and the file carries them", async () => {
  table.mockReturnValue(ok({ ...TABLE, period_start: null }) as never);

  renderPage(<CandidatesPage />, {
    url: "/?snapshot=11&min_confidence=0.5&date_from=all&include_administrative=1&criminal_only=1&event_date_filter=0"
  });

  expect(await screen.findByText(/Новости за всё время\./)).toBeTruthy();
  expect(table).toHaveBeenCalledWith({
    query: { ...DEFAULT_QUERY, snapshot_id: 11, min_confidence: 0.5, date_from: "", include_administrative: true, criminal_only: true, event_date_filter: false }
  });
  expect(screen.getByRole("link", { name: "Скачать Excel" }).getAttribute("href")).toBe(
    "/ui/candidates/export.xlsx?snapshot_id=12&min_confidence=0.5&date_from=&include_administrative=1&criminal_only=1&event_date_filter=0"
  );
});

it("says when nobody passes the filters", async () => {
  table.mockReturnValue(ok({ ...TABLE, total: 0, items: [] }) as never);

  renderPage(<CandidatesPage />);

  expect(await screen.findByText("Кандидатов при этих условиях нет.")).toBeTruthy();
});

it("shows the API's own error, as with no snapshot loaded", async () => {
  table.mockReturnValue(failed(404, "Snapshot Росфинмониторинга ещё не загружен.") as never);

  renderPage(<CandidatesPage />);

  expect(await screen.findByText("Snapshot Росфинмониторинга ещё не загружен.")).toBeTruthy();
});
