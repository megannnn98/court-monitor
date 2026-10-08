import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { LegacyPage } from "@/components/LegacyPage";
import { pieceOf } from "@/lib/legacy";
import { LogsPage } from "@/pages/LogsPage";
import { RfmPage } from "@/pages/RfmPage";
import { RunsPage } from "@/pages/RunsPage";
import { SentencesPage } from "@/pages/SentencesPage";
import { renderPage } from "@/test/render";

vi.mock("@/api/generated", () => ({ getStatusV1: vi.fn(() => new Promise(() => undefined)) }));

/** A legacy page as the server renders it: the menu, the head, the piece between its marks. */
function legacy(piece: string, title = "Журнал запусков"): string {
  return `<!doctype html><html><body><aside><nav><a href="/ui/cycle">Работа</a></nav></aside><main>
    <header class="page-head"><h1>${title}</h1>
      <details class="hint"><summary>Как это работает</summary>
        <div class="hint-body"><p>Пять шагов по кругу.</p><p><strong>Дальше:</strong> ждать</p></div></details>
    </header>
    <!--piece-->${piece}<!--/piece-->
  </main></body></html>`;
}

const JOURNAL = `<form method="post" action="/ui/management/run" class="source-form band"><h2>Запуск</h2>
  <button type="submit" formaction="/ui/management/purge">2. Очистить от мусора</button>
  <input type="date" name="published_from" value="2026-10-01"></form>
  <section class="band"><h2>Последние ручные запуски</h2><a href="/ui/runs?run_id=71">#71</a>
  <a href="/ui/political">Результат</a></section>`;

const fetched = vi.fn<typeof fetch>();

function answer(body: string, { status = 200, url = "", redirected = false } = {}): Response {
  const response = new Response(body, { status });
  Object.defineProperty(response, "url", { value: url });
  Object.defineProperty(response, "redirected", { value: redirected });
  return response;
}

beforeEach(() => {
  fetched.mockReset();
  vi.stubGlobal("fetch", fetched);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function requested(): [string, RequestInit | undefined][] {
  return fetched.mock.calls.map(([address, init]) => [String(address), init]);
}

it("shows the legacy page's own piece under its name, asked for as the console", async () => {
  fetched.mockResolvedValue(answer(legacy(JOURNAL)));

  renderPage(<LegacyPage legacy="/ui/runs" path="/runs" title="…" />, { path: "/runs", url: "/runs?run_id=72" });

  expect(await screen.findByRole("heading", { level: 1, name: "Журнал запусков" })).toBeTruthy();
  expect(screen.getByRole("heading", { name: "Последние ручные запуски" })).toBeTruthy();
  // The page's query goes to the legacy page; the header keeps it from being moved to React.
  expect(requested()[0][0]).toBe("/ui/runs?run_id=72");
  expect(requested()[0][1]?.headers).toEqual({ "X-Console-Piece": "1" });
  // Not the page around the piece: the console has its own menu.
  expect(screen.queryByRole("link", { name: "Работа" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /Как это работает/ }));
  expect(screen.getByText("Пять шагов по кругу.")).toBeTruthy();
});

it("sends an action to its legacy route and opens the answer here", async () => {
  // By what is asked, not by the order: the page may be read again at any moment.
  fetched.mockImplementation((address, init) =>
    Promise.resolve(
      init?.method === "POST"
        ? answer("", { url: "http://localhost/runs?run_id=73", redirected: true })
        : answer(legacy(String(address).includes("run_id=73") ? '<section class="band"><h2>Запуск #73</h2></section>' : JOURNAL))
    )
  );
  renderPage(<LegacyPage legacy="/ui/runs" path="/runs" title="…" />, { path: "/runs", url: "/runs" });

  fireEvent.click(await screen.findByRole("button", { name: "2. Очистить от мусора" }));

  expect(await screen.findByRole("heading", { name: "Запуск #73" })).toBeTruthy();
  await waitFor(() => expect(requested().at(-1)?.[0]).toBe("/ui/runs?run_id=73"));
  const sent = requested().filter(([, init]) => init?.method === "POST");
  // One request, to the pressed button's own route, with the form's fields.
  expect(sent).toHaveLength(1);
  expect(sent[0][0]).toBe("/ui/management/purge");
  expect(String(sent[0][1]?.body)).toBe("published_from=2026-10-01");
});

it("says a refusal in the legacy page's own words and stays", async () => {
  fetched.mockImplementation((_address, init) =>
    Promise.resolve(
      init?.method === "POST" ? answer(legacy('<p class="warning">Идёт другой запуск.</p>'), { status: 409 }) : answer(legacy(JOURNAL))
    )
  );
  renderPage(<LegacyPage legacy="/ui/runs" path="/runs" title="…" />, { path: "/runs", url: "/runs" });

  fireEvent.click(await screen.findByRole("button", { name: "2. Очистить от мусора" }));

  expect((await screen.findByRole("alert")).textContent).toBe("Идёт другой запуск.");
  expect(screen.getByRole("heading", { name: "Последние ручные запуски" })).toBeTruthy();
});

it("opens a link to the same page here and leaves any other to the browser", async () => {
  fetched.mockResolvedValue(answer(legacy(JOURNAL)));
  renderPage(<LegacyPage legacy="/ui/runs" path="/runs" title="…" />, { path: "/runs", url: "/runs" });

  const elsewhere = fireEvent.click(await screen.findByRole("link", { name: "Результат" }));
  const here = fireEvent.click(screen.getByRole("link", { name: "#71" }));

  // `fireEvent` answers false when the click was taken (preventDefault).
  expect([elsewhere, here]).toEqual([true, false]);
  await waitFor(() => expect(requested().at(-1)?.[0]).toBe("/ui/runs?run_id=71"));
});

it("a form that asks changes the address here", async () => {
  const filters = '<form method="get" class="toolbar"><select name="reason"><option value="religion" selected>вера</option></select><button type="submit">Показать</button></form>';
  fetched.mockResolvedValue(answer(legacy(filters, "Приговоры")));
  renderPage(<LegacyPage legacy="/ui/sentences" path="/sentences" title="…" />, { path: "/sentences", url: "/sentences" });

  fireEvent.click(await screen.findByRole("button", { name: "Показать" }));

  await waitFor(() => expect(requested().at(-1)?.[0]).toBe("/ui/sentences?reason=religion"));
});

it("copies a name and opens a link out of the console in a new tab", async () => {
  const write = vi.fn(() => Promise.resolve());
  vi.stubGlobal("navigator", { clipboard: { writeText: write } });
  const piece = '<button type="button" data-copy="Иванов Иван">⧉</button><a href="https://example.org/a">источник</a><a href="/ui/political">Результат</a>';
  fetched.mockResolvedValue(answer(legacy(piece)));
  renderPage(<LegacyPage legacy="/ui/rfm" path="/rfm" title="…" />, { path: "/rfm", url: "/rfm" });

  fireEvent.click(await screen.findByRole("button", { name: "⧉" }));

  expect(write).toHaveBeenCalledWith("Иванов Иван");
  await waitFor(() => expect(screen.getByRole("link", { name: "источник" }).getAttribute("target")).toBe("_blank"));
  expect(screen.getByRole("link", { name: "Результат" }).getAttribute("target")).toBeNull();
});

it("sends nothing when the form's own question was answered «Отмена»", async () => {
  fetched.mockResolvedValue(answer(legacy('<form method="post" action="/ui/management/runs/72/stop"><button type="submit">Остановить</button></form>')));
  renderPage(<LegacyPage legacy="/ui/logs" path="/logs" title="…" />, { path: "/logs", url: "/logs" });
  const button = await screen.findByRole("button", { name: "Остановить" });
  // What `onsubmit="return confirm(…)"` does when the operator says no.
  button.closest("form")?.addEventListener("submit", (event) => event.preventDefault());

  fireEvent.click(button);

  await new Promise((resolve) => setTimeout(resolve, 50));
  expect(requested().some(([, init]) => init?.method === "POST")).toBe(false);
});

it("gives the piece's tables to the legacy sorter, each piece once", async () => {
  const init = vi.fn();
  vi.stubGlobal("TableSort", { init });
  fetched.mockResolvedValueOnce(answer(legacy('<table><thead><tr><th>Имя</th></tr></thead><tbody><tr><td>а</td></tr></tbody></table><a href="/ui/rfm?days=7">7 дней</a>')));
  renderPage(<LegacyPage legacy="/ui/rfm" path="/rfm" title="…" />, { path: "/rfm", url: "/rfm" });
  await waitFor(() => expect(init).toHaveBeenCalledTimes(1));
  // The piece itself, not the document: the console's own tables are not touched.
  expect((init.mock.calls[0][0] as HTMLElement).className).toBe("legacy-html");

  fetched.mockResolvedValue(answer(legacy('<table><thead><tr><th>Имя</th></tr></thead><tbody><tr><td>б</td></tr></tbody></table>')));
  fireEvent.click(screen.getByRole("link", { name: "7 дней" }));

  await waitFor(() => expect(init).toHaveBeenCalledTimes(2));
});

it("reads a run as live by the page's own script, not by a log that holds the same words", () => {
  const log = '<pre class="log">window.location.reload() &lt;!--/piece--&gt;</pre>';
  const live = "<script>setTimeout(() => window.location.reload(), 5000);</script>";

  expect(pieceOf(legacy(log))).toMatchObject({ live: false, html: log });
  expect(pieceOf(legacy(log + live))?.live).toBe(true);
  expect(pieceOf("<html><body>нет меток</body></html>")).toBeNull();
});

it("says a page the server does not have", async () => {
  fetched.mockResolvedValue(answer('{"detail":"Запуск не найден"}', { status: 404 }));

  renderPage(<LegacyPage legacy="/ui/runs" path="/runs" title="Журнал запусков" />, { path: "/runs", url: "/runs?run_id=999" });

  expect(await screen.findByText("Запуск не найден")).toBeTruthy();
  expect(screen.getByRole("heading", { level: 1, name: "Журнал запусков" })).toBeTruthy();
});

it.each([
  [<RunsPage key="runs" />, "/runs", "/ui/runs"],
  [<LogsPage key="logs" />, "/logs", "/ui/logs"],
  [<RfmPage key="rfm" />, "/rfm", "/ui/rfm"],
  [<SentencesPage key="sentences" />, "/sentences", "/ui/sentences"]
])("the page at %s is the legacy page %s", async (page, path, address) => {
  fetched.mockResolvedValue(answer(legacy("<p>содержимое</p>")));

  renderPage(page, { path, url: `${path}?page=2` });

  expect(await screen.findByText("содержимое")).toBeTruthy();
  expect(requested()[0][0]).toBe(`${address}?page=2`);
});
