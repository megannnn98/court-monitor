import { type FormEvent, type MouseEvent, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { refusalOf } from "@/lib/legacy";

import "@/legacy-html.css";

/** The page a piece is of: its legacy address and its address in the console. */
export type Here = { legacy: string; path: string };

/** An address of this very page, legacy or the console's, as the console's; None for
 * any other address — that one the browser opens, and the server knows where it lives. */
export function sameIn(here: Here, url: string): string | null {
  const { pathname, search, hash } = new URL(url, window.location.origin);
  return pathname === here.legacy || pathname === here.path ? `${here.path}${search}${hash}` : null;
}

function fieldsOf(form: HTMLFormElement, submitter: HTMLElement | null): URLSearchParams {
  const fields = new URLSearchParams();
  for (const [name, value] of new FormData(form, submitter)) {
    fields.append(name, String(value));
  }
  return fields;
}

type Sorter = { init: (root: ParentNode) => void };

function script(src: string): Promise<void> {
  return new Promise((resolve) => {
    const tag = document.createElement("script");
    tag.src = src;
    tag.onload = () => resolve();
    tag.onerror = () => resolve();
    document.head.appendChild(tag).remove();
  });
}

let sorter: Promise<Sorter | undefined> | undefined;

/** The legacy pages' own sorting of tables (`src/static/table-sort*.js`), loaded once
 * for the console however many pieces ask for it. */
function tableSorter(): Promise<Sorter | undefined> {
  const loaded = () => (window as { TableSort?: Sorter }).TableSort;
  sorter ??= loaded()
    ? Promise.resolve(loaded())
    : script("/static/table-sort-core.js")
        .then(() => script("/static/table-sort.js"))
        .then(loaded);
  return sorter;
}

/** What the legacy pages' own scripts do once a page is loaded (`src/static/local-ui.js`,
 * `table-sort.js`, `web.ui.logs`), done for a piece and for nothing outside it: a link
 * out of the console opens in a new tab, a log is shown at its last lines, and every
 * table sorts by a press on a column's header. */
async function enhance(piece: HTMLElement) {
  for (const link of piece.querySelectorAll<HTMLAnchorElement>('a[href^="http"]')) {
    if (link.host !== window.location.host) {
      link.target = "_blank";
      link.rel = "noopener noreferrer";
    }
  }
  for (const log of piece.querySelectorAll<HTMLElement>("pre.log")) {
    log.scrollTop = log.scrollHeight;
  }
  if (piece.querySelector("table")) {
    const found = await tableSorter();
    // The piece may have gone while the scripts loaded: its page was left.
    if (piece.isConnected) {
      found?.init(piece);
    }
  }
}

/** A piece of a legacy page, shown as the server renders it and in the legacy console's
 * own styles (`legacy-html.css`), so it is the same piece. The server escapes the base's
 * text when it renders.
 *
 * The piece works in place: a form that asks (GET) and a link to this page change the
 * address here; a form that acts (POST) is sent to its legacy route, the piece is read
 * again, and a refusal is said above it. The page is never left for the legacy one. */
export function LegacyHtml({ html, here, onDone }: { html: string; here: Here; onDone?: () => Promise<unknown> | void }) {
  const navigate = useNavigate();
  const [refused, setRefused] = useState("");
  const [busy, setBusy] = useState(false);
  const piece = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (piece.current) {
      void enhance(piece.current);
    }
  }, [html]);

  async function submit(event: FormEvent<HTMLDivElement>) {
    // The form's own «Остановить запуск?» was answered «Отмена»: nothing is sent.
    if (event.defaultPrevented) {
      return;
    }
    const form = event.target as HTMLFormElement;
    const submitter = (event.nativeEvent as SubmitEvent).submitter;
    const action = submitter?.getAttribute("formaction") ?? form.getAttribute("action") ?? here.legacy;
    const fields = fieldsOf(form, submitter);
    if (form.method.toLowerCase() !== "post") {
      const asked = sameIn(here, action.split("?")[0]);
      if (asked !== null) {
        event.preventDefault();
        navigate(`${here.path}?${fields}`);
      }
      return;
    }
    event.preventDefault();
    if (busy) {
      return;
    }
    setBusy(true);
    setRefused("");
    try {
      const answer = await fetch(action, { method: "POST", body: fields });
      if (!answer.ok) {
        setRefused(refusalOf(await answer.text()) || `Сервер ответил ${answer.status}`);
        return;
      }
      const next = sameIn(here, answer.url);
      if (next !== null) {
        navigate(next);
      } else if (answer.redirected) {
        window.location.assign(answer.url);
      }
      // Read again what the action changed; the page does not wait for it.
      void Promise.resolve(onDone?.()).catch(() => undefined);
    } catch {
      setRefused("Сервер не ответил.");
    } finally {
      setBusy(false);
    }
  }

  // A link to this page opens here, without loading the console again; a click that asks
  // for another tab is the browser's.
  function follow(event: MouseEvent<HTMLDivElement>) {
    // «Скопировать» beside a name: the name goes to the clipboard as it is shown.
    const copy = (event.target as HTMLElement).closest<HTMLButtonElement>("button[data-copy]");
    if (copy && navigator.clipboard) {
      void navigator.clipboard.writeText(copy.dataset.copy ?? "").then(() => {
        copy.classList.add("copied");
        setTimeout(() => copy.classList.remove("copied"), 1200);
      });
      return;
    }
    const plain = event.button === 0 && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey;
    const link = (event.target as HTMLElement).closest("a");
    const next = link?.getAttribute("href") && !link.target ? sameIn(here, link.href) : null;
    if (plain && next !== null && !event.defaultPrevented) {
      event.preventDefault();
      navigate(next);
    }
  }

  return (
    <>
      {refused ? (
        <p role="alert" className="mb-3 text-sm text-destructive">
          {refused}
        </p>
      ) : null}
      <div ref={piece} className="legacy-html" aria-busy={busy} onSubmit={submit} onClick={follow} dangerouslySetInnerHTML={{ __html: html }} />
    </>
  );
}
