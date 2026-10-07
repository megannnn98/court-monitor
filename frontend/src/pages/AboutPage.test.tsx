import { screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { getAboutV1 } from "@/api/generated";
import { AboutPage } from "@/pages/AboutPage";
import { failed, ok, renderPage } from "@/test/render";

vi.mock("@/api/generated", () => ({ getAboutV1: vi.fn() }));
const about = vi.mocked(getAboutV1);

const ABOUT = {
  version: "0.36.0",
  tag: "0.36.0-3-g495d9e9",
  commit: "495d9e9",
  built_at: "2026-10-07T08:00:00Z",
  articles: 128450,
  people: 13765,
  last_successful_run_at: "2026-10-07T07:00:00Z",
  checked_at: "2026-10-07T09:00:00Z"
};

beforeEach(() => about.mockReset());

it("shows the build and the totals", async () => {
  about.mockReturnValue(ok(ABOUT) as never);

  renderPage(<AboutPage />);

  expect(await screen.findByText("0.36.0-3-g495d9e9")).toBeTruthy();
  expect(screen.getByText(/^128\s450$/)).toBeTruthy();
  expect(screen.getByText(/^13\s765$/)).toBeTruthy();
  expect(screen.getByText("Последний успешный запуск")).toBeTruthy();
});

it("says «неизвестно» when no run has succeeded", async () => {
  about.mockReturnValue(ok({ ...ABOUT, last_successful_run_at: null }) as never);

  renderPage(<AboutPage />);

  expect(await screen.findByText("неизвестно")).toBeTruthy();
});

it("shows the API's own error", async () => {
  about.mockReturnValue(failed(500, "database unavailable") as never);

  renderPage(<AboutPage />);

  expect(await screen.findByText("database unavailable")).toBeTruthy();
});
