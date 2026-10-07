import { screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { getArticleMentionsV1, getArticleV1 } from "@/api/generated";
import { ArticlePage } from "@/pages/ArticlePage";
import { failed, ok, renderPage } from "@/test/render";

vi.mock("@/api/generated", () => ({ getArticleV1: vi.fn(), getArticleMentionsV1: vi.fn() }));
const article = vi.mocked(getArticleV1);
const mentions = vi.mocked(getArticleMentionsV1);

const ARTICLE = {
  id: 77,
  source_name: "ovd",
  external_id: "e1",
  title: "Приговор Орлову",
  published_at: "2024-02-27T10:00:00+00:00",
  url: "https://ovd.info/news/1",
  text: "Сегодня суд приговорил Олега Орлова к сроку."
};

beforeEach(() => {
  article.mockReset();
  mentions.mockReset();
  mentions.mockReturnValue(ok({ people: [], events: [] }) as never);
});

it("shows the text with the quoted span marked", async () => {
  article.mockReturnValue(ok(ARTICLE) as never);

  const { container } = renderPage(<ArticlePage />, { path: "/articles/:articleId", url: "/articles/77?start=8&end=22" });

  expect(await screen.findByRole("heading", { name: "Приговор Орлову" })).toBeTruthy();
  expect(container.querySelector("mark")?.textContent).toBe("суд приговорил");
  expect(screen.getByRole("link", { name: "https://ovd.info/news/1" }).getAttribute("rel")).toBe("noopener noreferrer");
});

it("marks nothing for a span outside the text and links no odd address", async () => {
  article.mockReturnValue(ok({ ...ARTICLE, url: "javascript:alert(1)" }) as never);

  const { container } = renderPage(<ArticlePage />, { path: "/articles/:articleId", url: "/articles/77?start=8&end=999" });

  await screen.findByRole("heading", { name: "Приговор Орлову" });
  expect(container.querySelector("mark")).toBeNull();
  expect(screen.queryByRole("link", { name: /javascript/ })).toBeNull();
});

it("shows the API's own error", async () => {
  article.mockReturnValue(failed(404, "Article not found") as never);

  renderPage(<ArticlePage />, { path: "/articles/:articleId", url: "/articles/1" });

  expect(await screen.findByText("Article not found")).toBeTruthy();
});

it("shows the people the article names and its events", async () => {
  article.mockReturnValue(ok(ARTICLE) as never);
  mentions.mockReturnValue(
    ok({
      people: [{ key: "олег орлов", name: "Орлов Олег", dossier_url: "/ui/investigations/%D0%BE" }],
      events: [{ kind: "sentence", label: "Приговор", count: 2 }]
    }) as never
  );

  renderPage(<ArticlePage />, { path: "/articles/:articleId", url: "/articles/77" });

  expect((await screen.findByRole("link", { name: "Орлов Олег" })).getAttribute("href")).toBe("/ui/investigations/%D0%BE");
  expect(screen.getByText("Приговор: 2")).toBeTruthy();
  expect(mentions).toHaveBeenCalledWith({ path: { article_id: 77 } });
});
