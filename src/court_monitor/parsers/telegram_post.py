"""Parser for Telegram public web previews (``https://t.me/s/<channel>``).

The ``t.me/s/<channel>`` endpoint returns a static HTML page listing the most
recent posts of a public channel. Each post is a self-contained material with a
stable permalink (``t.me/<channel>/<messageId>``), a publication timestamp, and
the full message text — exactly the fields we need.

A *single* post lives inside one ``.tgme_widget_message_wrap`` node. The same
node shape is used both on the preview page (many posts) and on an individual
post page, so one parser covers both.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime

from selectolax.parser import HTMLParser, Node

PARSER_VERSION = "telegram-post-0.1"

# A post is always parsed from a single selectolax Node (an element within the
# document), never the document root. We keep a narrow alias for readability.
Scope = Node

_WRAP = ".tgme_widget_message_wrap"
_MSG = ".tgme_widget_message"
_TEXT = ".tgme_widget_message_text"
_DATE = ".tgme_widget_message_date"
_AUTHOR = ".tgme_widget_message_author"


@dataclass(frozen=True)
class ParsedTelegramPost:
    post_id: str | None
    permalink: str | None
    published_at: datetime | None
    text: str
    channel: str | None
    author: str | None
    html: str
    parser_version: str = PARSER_VERSION


def parse_channel_preview(html: str) -> list[ParsedTelegramPost]:
    """Parse a ``t.me/s/<channel>`` preview page into its posts (newest first)."""
    tree = HTMLParser(html)
    out: list[ParsedTelegramPost] = []
    seen: set[str] = set()
    for node in tree.css(_WRAP):
        post = _parse_post_node(node)
        if post is None:
            continue
        key = post.post_id or post.permalink or post.text[:64]
        if key in seen:
            continue
        seen.add(key)
        out.append(post)
    return out


def parse_single_post(html: str) -> ParsedTelegramPost | None:
    """Parse a single post node (e.g. a saved fixture of one message wrap)."""
    tree = HTMLParser(html)
    node = tree.css_first(_WRAP) or tree.css_first(_MSG)
    if node is None:
        return None
    return _parse_post_node(node)


def _parse_post_node(wrap: Scope) -> ParsedTelegramPost | None:
    # The wrap may be the outer .tgme_widget_message_wrap (preview / single) or,
    # as a fallback, the inner .tgme_widget_message node itself.
    inner = wrap.css_first(_MSG) or wrap

    data_post_attr = inner.attributes.get("data-post")
    data_post = (data_post_attr or "").strip() if isinstance(data_post_attr, str) else ""
    channel: str | None = None
    post_id: str | None = None
    if data_post and "/" in data_post:
        chan_part, _, id_part = data_post.partition("/")
        channel = chan_part or None
        post_id = id_part or None

    permalink = _href(inner, _DATE) or _href(wrap, _DATE)
    if permalink and (channel is None or post_id is None):
        m = re.search(r"/([^/]+)/(\d+)/?$", permalink)
        if m:
            channel = channel or m.group(1)
            post_id = post_id or m.group(2)

    published_at = _datetime(inner) or _datetime(wrap)

    text_node = inner.css_first(_TEXT) or wrap.css_first(_TEXT)
    text = text_node.text(separator=" ", strip=True) if text_node is not None else ""
    text = _clean(text)

    author_node = inner.css_first(_AUTHOR) or wrap.css_first(_AUTHOR)
    author = author_node.text(strip=True) if author_node is not None else None

    return ParsedTelegramPost(
        post_id=post_id,
        permalink=permalink,
        published_at=published_at,
        text=text,
        channel=channel,
        author=author,
        html=(wrap.html or "").strip(),
        parser_version=PARSER_VERSION,
    )


def _href(scope: Scope, selector: str) -> str | None:
    node = scope.css_first(selector)
    if node is None:
        return None
    href = node.attributes.get("href")
    return href.strip() if isinstance(href, str) and href.strip() else None


def _datetime(scope: Scope) -> datetime | None:
    node = scope.css_first("time[datetime]")
    if node is None:
        return None
    value = node.attributes.get("datetime")
    if not isinstance(value, str) or not value.strip():
        return None
    return _parse_iso(value.strip())


def _parse_iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        # Telegram also embeds a unix timestamp in data-view (base64 JSON).
        pass
    return None


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def decode_data_view(raw: str | None) -> dict[str, object] | None:
    """Best-effort decode of the base64 ``data-view`` attribute on a post.

    Returned for diagnostics only; never used for extraction of facts.
    """
    if not raw:
        return None
    try:
        payload = json.loads(__import__("base64").b64decode(raw).decode("utf-8"))
    except Exception:  # noqa: BLE001 - best effort, diagnostics only
        return None
    return payload if isinstance(payload, dict) else None
