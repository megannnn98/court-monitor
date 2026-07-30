"""Read the source registry from a public Airtable shared view.

The shared view at ``https://airtable.com/<base>/<sharedViewId>`` is JavaScript
rendered: a plain HTTP request returns the application shell only. We therefore
split the work in two:

* :func:`parse_airtable_shared_view` — a **pure** HTML parser (selectolax) that
  turns the *rendered* DOM into :class:`RawRegistryRow` values. It is fully
  unit-testable on a saved fixture and has no network/playwright dependency.
* :func:`render_shared_view` — the live renderer (Playwright, lazy-imported).
  Used only by the ``--from-airtable`` CLI path. It launches a cached
  Chromium, waits for the grid, scrolls to defeat row virtualisation, and
  returns the rendered HTML.

A manually exported CSV (``File → Download records → CSV``) is also supported
via :func:`parse_registry_csv` for environments without Playwright or when the
shared view cannot be read automatically.

Nothing here bypasses auth/captcha — only **public** shared views are read, and
only via the same DOM a human visitor would see.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from selectolax.parser import HTMLParser

if TYPE_CHECKING:
    from typing import Any

DEFAULT_REGISTRY_VIEW_URL = "https://airtable.com/apppAy5vZCrpb53wc/shr2RcGSNfQPtqUPs"

# Header labels we recognise (Russian, as observed in the actual shared view).
H_NAME = "Название"
H_USERNAME = "Юзернейм"
H_LINK = "Ссылка"
H_TOPIC = "Тип/тематика"


@dataclass(frozen=True)
class RawRegistryRow:
    """One row of the Airtable shared view, exactly as observed (no normalisation)."""

    name: str | None
    username: str | None
    url: str | None
    topics: list[str] = field(default_factory=list)
    # free-form dict of every observed header -> raw cell text (auditability)
    fields: dict[str, str] = field(default_factory=dict)


def parse_airtable_shared_view(html: str) -> list[RawRegistryRow]:
    """Parse a *rendered* Airtable shared-view HTML into raw rows.

    The Airtable grid splits each record across two synchronized panes:
    the left pane holds the primary field (``Название``); the right pane holds
    the remaining fields, in the same vertical order. Headers come from
    ``.cell.header.read``.
    """
    tree = HTMLParser(html)

    headers = [_squash(h.text(separator=" ")) for h in tree.css(".cell.header.read")]
    headers = [h for h in headers if h]
    if not headers:
        return []

    left_rows = tree.css(".dataRow.leftPane")
    right_rows = tree.css(".dataRow.rightPane")

    out: list[RawRegistryRow] = []
    for left, right in _zip_strict(left_rows, right_rows):
        primary = left.css_first(".cell.primary.read")
        name = primary.text(separator=" ", strip=True) if primary is not None else ""
        if not name:
            continue

        # Non-primary cells live in the right pane, in header order minus primary.
        right_headers = [h for i, h in enumerate(headers) if i != _primary_index(headers)]
        fields: dict[str, str] = {}
        cell_nodes = right.css(".cell.read")
        for i, node in enumerate(cell_nodes):
            if i >= len(right_headers):
                break
            label = right_headers[i]
            if not label:
                continue
            # Airtable renders multi-select cells as several choiceToken spans.
            tokens = [t.text(separator=" ") for t in node.css(".choiceToken")]
            if tokens:
                fields[label] = "\n".join(_squash(t) for t in tokens if _squash(t))
            else:
                fields[label] = _squash(node.text(separator=" "))

        out.append(
            RawRegistryRow(
                name=_squash(name),
                username=_strip_at(fields.get(H_USERNAME)),
                url=_clean_url(fields.get(H_LINK)),
                topics=_split_topics(fields.get(H_TOPIC)),
                fields=fields,
            )
        )
    return out


def parse_registry_csv(path: str | Path) -> list[RawRegistryRow]:
    """Parse a CSV exported from the shared view.

    Tolerates header reordering and missing columns. Multi-select cells in the
    Airtable CSV export are newline-separated; we also accept ``", "`` (our own
    fixture formatting) and the pipe separator.
    """
    path = Path(path)
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        rows = list(reader)
    if not rows:
        return []

    header = [h.strip() for h in rows[0]]
    idx = {label: i for i, label in enumerate(header) if label}

    def cell(row: list[str], label: str) -> str | None:
        i = idx.get(label)
        if i is None or i >= len(row):
            return None
        value = row[i].strip()
        return value or None

    out: list[RawRegistryRow] = []
    for row in rows[1:]:
        if not any(c.strip() for c in row):
            continue
        fields = {label: row[i] for label, i in idx.items() if i < len(row)}
        out.append(
            RawRegistryRow(
                name=cell(row, H_NAME),
                username=_strip_at(cell(row, H_USERNAME)),
                url=_clean_url(cell(row, H_LINK)),
                topics=_split_topics(cell(row, H_TOPIC)),
                fields=fields,
            )
        )
    return out


def render_shared_view(
    url: str = DEFAULT_REGISTRY_VIEW_URL,
    *,
    timeout_ms: int = 60_000,
    settle_ms: int = 4_000,
    scroll_steps: int = 30,
) -> str:
    """Render a public Airtable shared view via Playwright and return HTML.

    Launches a headless Chromium (resolving a cached binary if the installed
    Playwright version mismatches the browser cache). Reads the same DOM a
    human visitor sees; performs no auth, no captcha, no clicks on protected
    controls — only scroll to reveal virtualised rows.
    """
    try:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - environment-specific
        raise RuntimeError(
            "Playwright is required to read the Airtable shared view. "
            "Install it (`uv sync --extra playwright` or `uv pip install playwright`) "
            "and run `playwright install chromium`, or export the view to CSV and use "
            "`import-source-registry --from-csv <file>`."
        ) from exc

    ua = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0 Safari/537.36 court-monitor-registry-import/0.1"
    )
    with sync_playwright() as p:
        kwargs: dict[str, Any] = {"headless": True}
        exe = _resolve_chromium(p)
        if exe is not None:
            kwargs["executable_path"] = exe
        browser = p.chromium.launch(**kwargs)
        try:
            page = browser.new_page(user_agent=ua, viewport={"width": 1500, "height": 1000})
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            # Airtable renders its grid client-side, so a fixed settle delay is a
            # race: lose it and this returns an empty shell that parses to zero
            # rows, which reads as "the view is empty" rather than "we were too
            # early". Wait for actual rows and fail loudly if they never arrive.
            try:
                page.wait_for_selector(".dataRow.leftPane", timeout=timeout_ms)
            except Exception as exc:
                raise RuntimeError(
                    f"Airtable view rendered no rows within {timeout_ms} ms ({url}). "
                    "The view may be empty or unreachable, or its markup may have "
                    "changed; export it to CSV and use --from-csv to bypass this."
                ) from exc
            page.wait_for_timeout(settle_ms)
            pane = page.locator(".dataRightPane.pane").first
            for _ in range(max(1, scroll_steps)):
                try:
                    pane.evaluate("e => e.scrollTop += 900")
                except Exception:  # noqa: BLE001 - pane may not exist on tiny views
                    break
                page.wait_for_timeout(220)
            return page.content()
        finally:
            browser.close()


def _resolve_chromium(p: Any) -> str | None:
    """Return a usable chromium executable path, preferring the default.

    Returns a cached binary when the installed Playwright version does not ship
    the exact browser revision present in the local cache (common in CI/dev
    where versions drift).
    """
    import glob  # noqa: PLC0415
    import os  # noqa: PLC0415

    default = p.chromium.executable_path
    if default and os.path.exists(default):
        return None  # let Playwright use its own default
    home = os.path.expanduser("~/.cache/ms-playwright")
    candidates = []
    for pat in (
        f"{home}/chromium-*/chrome-linux64/chrome",
        f"{home}/chromium-*/chrome-linux/chrome",
    ):
        candidates.extend(glob.glob(pat))
    if not candidates:
        return None
    candidates.sort(reverse=True)  # newest build first
    return candidates[0]


def _zip_strict(left: list[Any], right: list[Any]) -> Iterator[tuple[Any, Any]]:
    n = min(len(left), len(right))
    for i in range(n):
        yield left[i], right[i]


def _primary_index(headers: list[str]) -> int:
    for i, h in enumerate(headers):
        if h == H_NAME:
            return i
    return 0


def _squash(value: str) -> str:
    """Collapse all whitespace runs to single spaces and strip."""
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def _split_topics(value: str | None) -> list[str]:
    if not value:
        return []
    parts: list[str] = []
    for raw_chunk in value.replace("\r", "\n").replace(", ", "\n").replace("|", "\n").split("\n"):
        chunk = raw_chunk.strip()
        if chunk and chunk not in parts:
            parts.append(chunk)
    return parts


def _clean_url(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip()
    if not v:
        return None
    return v


def _strip_at(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip()
    return v.lstrip("@").strip() or None
