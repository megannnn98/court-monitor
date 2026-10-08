import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { getWikiPageV1, listWikiV1 } from "@/api/generated";
import { WikiPage } from "@/pages/WikiPage";
import { failed, ok, renderPage } from "@/test/render";

vi.mock("@/api/generated", () => ({ listWikiV1: vi.fn(), getWikiPageV1: vi.fn() }));
const list = vi.mocked(listWikiV1);
const get = vi.mocked(getWikiPageV1);

beforeEach(() => {
  list.mockReset();
  get.mockReset();
  list.mockReturnValue(ok([{ slug: "Home" }, { slug: "Setup" }]) as never);
  get.mockImplementation(({ path }) =>
    (path.slug === "Home"
      ? ok({ slug: "Home", html: '<h1>Court monitor</h1><p>См. <a href="/wiki/Setup">установку</a>.</p>' })
      : path.slug === "Setup"
        ? ok({ slug: "Setup", html: "<h1>Установка</h1>" })
        : failed(404, "Wiki page not found")) as never
  );
});

it("shows Home and follows a link to another page in place", async () => {
  renderPage(<WikiPage />, { path: "/wiki/:slug?", url: "/wiki" });

  expect(await screen.findByRole("heading", { name: "Court monitor" })).toBeTruthy();
  expect(screen.getByRole("link", { name: "Скачать вики в PDF" }).getAttribute("href")).toBe("/ui/wiki/export.pdf");
  fireEvent.click(screen.getByRole("link", { name: "установку" }));

  await waitFor(() => expect(get).toHaveBeenLastCalledWith({ path: { slug: "Setup" } }));
  expect(await screen.findByRole("heading", { name: "Установка" })).toBeTruthy();
});

it("says a page that is not there", async () => {
  renderPage(<WikiPage />, { path: "/wiki/:slug?", url: "/wiki/nothing" });

  expect(await screen.findByText("Wiki page not found")).toBeTruthy();
});
