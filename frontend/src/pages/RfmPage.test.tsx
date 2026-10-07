import { screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { listRosfinmonitoringEntriesV1, listRosfinmonitoringSnapshotsV1 } from "@/api/generated";
import { RfmPage } from "@/pages/RfmPage";
import { failed, ok, renderPage } from "@/test/render";

vi.mock("@/api/generated", () => ({ listRosfinmonitoringEntriesV1: vi.fn(), listRosfinmonitoringSnapshotsV1: vi.fn() }));
const entries = vi.mocked(listRosfinmonitoringEntriesV1);
const snapshots = vi.mocked(listRosfinmonitoringSnapshotsV1);

const SNAPSHOT = { id: 12, snapshot_date: "2026-10-01", source_url: "https://fedsfm.ru/documents/terrorists-catalog-portal-act", content_hash: "h", entry_count: 23001 };

beforeEach(() => {
  entries.mockReset();
  snapshots.mockReset();
});

it("shows the snapshots and the entries of the newest one", async () => {
  snapshots.mockReturnValue(ok([SNAPSHOT]) as never);
  entries.mockReturnValue(
    ok([{ id: 1, snapshot_id: 12, full_name: "ИВАНОВ ИВАН", normalized_name: "иванов иван", matching_key: "k", birth_date: "1980-01-01", inclusion_reason: "терроризм" }]) as never
  );

  renderPage(<RfmPage />);

  expect(await screen.findByText("ИВАНОВ ИВАН")).toBeTruthy();
  expect(screen.getByText("01.01.1980")).toBeTruthy();
  expect(screen.getByText(/^23\s001$/)).toBeTruthy();
  expect(entries).toHaveBeenCalledWith({ path: { snapshot_id: 12 }, query: { limit: 100, offset: 0 } });
});

it("takes the snapshot and the page from the address", async () => {
  snapshots.mockReturnValue(ok([SNAPSHOT]) as never);
  entries.mockReturnValue(ok([]) as never);

  renderPage(<RfmPage />, { url: "/?snapshot=7&page=2" });

  expect(await screen.findByText(/Строк нет/)).toBeTruthy();
  expect(entries).toHaveBeenCalledWith({ path: { snapshot_id: 7 }, query: { limit: 100, offset: 100 } });
});

it("says when no list has been loaded", async () => {
  snapshots.mockReturnValue(ok([]) as never);

  renderPage(<RfmPage />);

  expect(await screen.findByText("Перечень ещё не загружен.")).toBeTruthy();
  expect(entries).not.toHaveBeenCalled();
});

it("shows the API's own error", async () => {
  snapshots.mockReturnValue(failed(500, "no snapshots table") as never);

  renderPage(<RfmPage />);

  expect(await screen.findByText("no snapshots table")).toBeTruthy();
});
