import { screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { listPersonsV1 } from "@/api/generated";
import { PersonsPage } from "@/pages/PersonsPage";
import { failed, ok, renderPage } from "@/test/render";

vi.mock("@/api/generated", () => ({ listPersonsV1: vi.fn() }));
const list = vi.mocked(listPersonsV1);

beforeEach(() => list.mockReset());

it("lists the persons with a link to each card", async () => {
  list.mockReturnValue(
    ok([{ id: 7, canonical_name: "Иван Петров", normalized_name: "иван петров", matching_key: "k", status: "active" }]) as never
  );

  renderPage(<PersonsPage />);

  const link = await screen.findByRole("link", { name: "Иван Петров" });
  expect(link.getAttribute("href")).toBe("/persons/7");
  expect(list).toHaveBeenCalledWith({ query: { limit: 100, offset: 0, status: null } });
});

it("reads the page and the status from the address", async () => {
  list.mockReturnValue(ok([]) as never);

  renderPage(<PersonsPage />, { url: "/?page=3&status=merged" });

  await screen.findByText(/Строк нет/);
  expect(list).toHaveBeenCalledWith({ query: { limit: 100, offset: 200, status: "merged" } });
});

it("says when there is nobody", async () => {
  list.mockReturnValue(ok([]) as never);

  renderPage(<PersonsPage />);

  expect(await screen.findByText("Персон нет.")).toBeTruthy();
});

it("shows the API's own error", async () => {
  list.mockReturnValue(failed(500, "database is down") as never);

  renderPage(<PersonsPage />);

  expect(await screen.findByText("database is down")).toBeTruthy();
});
