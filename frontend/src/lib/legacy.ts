import { ApiError } from "@/lib/api";

/** Asked for with this header, a legacy page is served, not moved to React (`src/web/spa.py`). */
export const PIECE_HEADER = "X-Console-Piece";
/** Around what a legacy page shows under its head (`src/web/ui/layout.py`). */
const OPEN = "<!--piece-->";
const CLOSE = "<!--/piece-->";
/** A legacy page that shows a live run reloads itself; here it is read again instead. */
const RELOADS = "window.location.reload()";

export type LegacyPiece = {
  title: string;
  instruction: string;
  /** What the page shows under its head, as the server rendered it. */
  html: string;
  /** Something on the page is running: worth reading again in a few seconds. */
  live: boolean;
};

/** The piece of a legacy page, with the page's name and its «Как это работает». */
export function pieceOf(page: string): LegacyPiece | null {
  const start = page.indexOf(OPEN);
  const end = page.indexOf(CLOSE);
  if (start < 0 || end < start) {
    return null;
  }
  const html = page.slice(start + OPEN.length, end);
  const head = new DOMParser().parseFromString(page.slice(0, start), "text/html");
  // By the page's own script, not by its text: a log may hold the same words.
  const scripts = new DOMParser().parseFromString(html, "text/html").querySelectorAll("script");
  return {
    title: head.querySelector("h1")?.textContent?.trim() ?? "",
    instruction: head.querySelector(".hint-body p")?.textContent?.trim() ?? "",
    html,
    live: Array.from(scripts).some((script) => script.textContent?.includes(RELOADS))
  };
}

/** A legacy page's refusal in its own words: the `.warning` line, else the API's `detail`. */
export function refusalOf(body: string): string {
  const warning = new DOMParser().parseFromString(body, "text/html").querySelector(".warning");
  if (warning?.textContent?.trim()) {
    return warning.textContent.trim();
  }
  try {
    const detail = (JSON.parse(body) as { detail?: unknown }).detail;
    return typeof detail === "string" ? detail : "";
  } catch {
    return "";
  }
}

export async function readLegacyPage(address: string): Promise<LegacyPiece> {
  const answer = await fetch(address, { headers: { [PIECE_HEADER]: "1" } });
  const page = await answer.text();
  const piece = pieceOf(page);
  if (piece === null) {
    throw new ApiError(refusalOf(page) || `Сервер ответил ${answer.status}`, answer.status);
  }
  return piece;
}
