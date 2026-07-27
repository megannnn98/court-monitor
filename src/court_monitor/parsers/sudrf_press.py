"""Parser for sudrf.ru press-release pages.

Generic selectors covering the common platform layout and our synthetic
fixtures. Selectors are deliberately multi-candidate with safe fallbacks so a
minor markup change does not break extraction outright; full structural-change
handling (versioned parser + ReviewItem) is layered on top in Etap 2.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from selectolax.parser import HTMLParser

from court_monitor.extraction.dates import parse_russian_date

PARSER_VERSION = "sudrf-press-0.1"

# Candidate selectors, tried in order. First match wins.
_TITLE_SELECTORS = (
    "h1.press-title",
    "h1",
    ".press_title",
    "#news h1",
    "title",
)
_DATE_SELECTORS = (
    "time.press-date",
    ".press-date",
    ".press_date",
    ".b-date",
    "time[datetime]",
)
_BODY_SELECTORS = (
    ".press-body",
    ".press_body",
    "#news .b-text",
    ".b-text",
    "article",
    "body",
)


@dataclass
class ParsedPressRelease:
    title: str | None
    text: str
    published_at: date | None
    parser_version: str = PARSER_VERSION


def parse_press_release(html: str) -> ParsedPressRelease:
    tree = HTMLParser(html)

    title = _first_text(tree, _TITLE_SELECTORS)

    date_raw = _first_text(tree, _DATE_SELECTORS)
    dt_attr = _first_attr(tree, "time[datetime]", "datetime")
    published_at = None
    if dt_attr:
        published_at = parse_russian_date(dt_attr)
    if published_at is None and date_raw:
        published_at = parse_russian_date(date_raw)

    body_node = _first_node(tree, _BODY_SELECTORS)
    if body_node is not None:
        body_node.strip_tags(["script", "style"])
        text = _clean_text(body_node.text(separator=" ", strip=True))
    else:
        text = ""

    return ParsedPressRelease(
        title=(title or None),
        text=text,
        published_at=published_at,
    )


def _first_text(tree: HTMLParser, selectors: tuple[str, ...]) -> str | None:
    node = _first_node(tree, selectors)
    if node is None:
        return None
    return _clean_text(node.text(separator=" ", strip=True)) or None


def _first_node(tree: HTMLParser, selectors: tuple[str, ...]):
    for sel in selectors:
        node = tree.css_first(sel)
        if node is not None:
            return node
    return None


def _first_attr(tree: HTMLParser, selector: str, attr: str) -> str | None:
    node = tree.css_first(selector)
    if node is None:
        return None
    value = node.attributes.get(attr)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def to_datetime(d: date | None) -> datetime | None:
    return datetime(d.year, d.month, d.day) if d is not None else None
