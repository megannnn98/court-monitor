import { screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { listCandidatesV1, listRosfinmonitoringSnapshotsV1 } from "@/api/generated";
import { CandidatesPage } from "@/pages/CandidatesPage";
import { failed, ok, renderPage } from "@/test/render";

vi.mock("@/api/generated", () => ({ listCandidatesV1: vi.fn(), listRosfinmonitoringSnapshotsV1: vi.fn() }));
const candidates = vi.mocked(listCandidatesV1);
const snapshots = vi.mocked(listRosfinmonitoringSnapshotsV1);

const SNAPSHOTS = [
  { id: 12, snapshot_date: "2026-10-01", source_url: "https://fedsfm.ru", content_hash: "h", entry_count: 23000 },
  { id: 11, snapshot_date: "2026-09-01", source_url: "https://fedsfm.ru", content_hash: "g", entry_count: 22900 }
];
const CANDIDATE = {
  person_id: 5,
  canonical_name: "Анна Смирнова",
  normalized_name: "анна смирнова",
  persecution_status: "political",
  persecution_confidence: 0.91,
  persecution_reasons: ["antiwar_speech", "article_207_3"],
  rosfinmonitoring_status: "not_matched",
  event_count: 3,
  alias_count: 2
};

beforeEach(() => {
  candidates.mockReset();
  snapshots.mockReset();
  snapshots.mockReturnValue(ok(SNAPSHOTS) as never);
});

it("opens on the newest snapshot and shows its candidates", async () => {
  candidates.mockReturnValue(ok([CANDIDATE]) as never);

  renderPage(<CandidatesPage />);

  expect((await screen.findByRole("link", { name: "Анна Смирнова" })).getAttribute("href")).toBe("/persons/5");
  expect(screen.getByText("antiwar_speech, article_207_3")).toBeTruthy();
  expect(screen.getByText("0.91")).toBeTruthy();
  expect(candidates).toHaveBeenCalledWith({ query: { snapshot_id: 12, min_persecution_confidence: 0.7, limit: 100 } });
  expect(screen.getByRole("link", { name: "Скачать Excel" }).getAttribute("href")).toBe(
    "/ui/candidates/export.xlsx?snapshot_id=12&min_confidence=0.7"
  );
});

it("takes the snapshot and the filters from the address", async () => {
  candidates.mockReturnValue(ok([CANDIDATE]) as never);

  renderPage(<CandidatesPage />, { url: "/?snapshot=11&min_confidence=0.5&limit=10" });

  await screen.findByRole("link", { name: "Анна Смирнова" });
  expect(candidates).toHaveBeenCalledWith({ query: { snapshot_id: 11, min_persecution_confidence: 0.5, limit: 10 } });
});

it("says when nobody passes the filters", async () => {
  candidates.mockReturnValue(ok([]) as never);

  renderPage(<CandidatesPage />);

  expect(await screen.findByText("Кандидатов при этих условиях нет.")).toBeTruthy();
});

it("says there is no snapshot instead of asking for candidates", async () => {
  snapshots.mockReturnValue(ok([]) as never);

  renderPage(<CandidatesPage />);

  expect(await screen.findByText(/Snapshot Росфинмониторинга ещё не загружен/)).toBeTruthy();
  expect(candidates).not.toHaveBeenCalled();
});

it("shows the API's own error", async () => {
  candidates.mockReturnValue(failed(404, "Snapshot not found") as never);

  renderPage(<CandidatesPage />);

  expect(await screen.findByText("Snapshot not found")).toBeTruthy();
});
