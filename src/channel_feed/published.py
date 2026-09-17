"""The people the customer's channel has already written about.

The channel (@enbv2022) is the output of this work, never a source of candidates: it is
read only to leave out of the queue the people it has already published.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

import httpx
from selectolax.parser import HTMLParser

from extraction.extractors import RuleBasedEntityExtractor
from extraction.models import EntityType, ExtractionDocument
from extraction.normalizers import RuleBasedMentionNormalizer

CHANNEL_USERNAME = "enbv2022"
_PATRONYMIC_ENDINGS = ("вич", "вна", "ична", "чна")
# The channel is small; a guard against a preview that never stops paging.
_MAX_PAGES = 50


@dataclass(frozen=True)
class PublishedPost:
    post_id: int
    published_at: datetime
    text: str


def name_key(name: str) -> str | None:
    """Given name and surname in any order, without a patronymic or initials.

    «Ярош Сергей Васильевич» and «Сергей Ярош» give the same key; a single word gives
    none, since it does not name one person.
    """
    words = sorted(
        word
        for word in re.findall(r"[а-яёa-z-]+", name.lower().replace("ё", "е"))
        if len(word) > 1 and not word.endswith(_PATRONYMIC_ENDINGS)
    )
    return " ".join(words) if len(words) >= 2 else None


def parse_channel_page(html: bytes, username: str = CHANNEL_USERNAME) -> list[PublishedPost]:
    posts: list[PublishedPost] = []
    for node in HTMLParser(html).css("div.tgme_widget_message[data-post]"):
        channel, _, raw_id = (node.attributes.get("data-post") or "").partition("/")
        text_node = node.css_first(".tgme_widget_message_text")
        time_node = node.css_first(".tgme_widget_message_date time[datetime]")
        if channel.lower() != username or not raw_id.isdigit() or not text_node or not time_node:
            continue
        for line_break in text_node.css("br"):
            line_break.replace_with("\n")
        posts.append(
            PublishedPost(
                post_id=int(raw_id),
                published_at=datetime.fromisoformat(time_node.attributes["datetime"] or ""),
                text=text_node.text(),
            )
        )
    return sorted(posts, key=lambda post: post.post_id, reverse=True)


def fetch_channel_posts(
    client: httpx.Client, username: str = CHANNEL_USERNAME
) -> list[PublishedPost]:
    """Every post of the channel's public web preview, newest first."""
    posts: list[PublishedPost] = []
    before: int | None = None
    for _ in range(_MAX_PAGES):
        url = f"https://t.me/s/{username}" + (f"?before={before}" if before else "")
        response = client.get(url)
        response.raise_for_status()
        page = [
            post
            for post in parse_channel_page(response.content, username)
            if before is None or post.post_id < before
        ]
        if not page:
            break
        posts.extend(page)
        before = page[-1].post_id
    return posts


_cache: tuple[float, frozenset[str]] | None = None


def load_published_keys(ttl_seconds: float = 3600.0) -> frozenset[str]:
    """The channel's published name keys, fetched at most once per `ttl_seconds`."""
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < ttl_seconds:
        return _cache[1]
    with httpx.Client(timeout=30.0, headers={"User-Agent": "court-monitor/1.0"}) as client:
        keys = frozenset(published_name_keys(fetch_channel_posts(client)))
    _cache = (now, keys)
    return keys


def published_name_keys(posts: Iterable[PublishedPost]) -> set[str]:
    """The name keys of every person the posts name in full."""
    extractor = RuleBasedEntityExtractor()
    normalizer = RuleBasedMentionNormalizer()
    keys: set[str] = set()
    for post in posts:
        document = ExtractionDocument(
            article_id=post.post_id,
            title="",
            text=post.text,
            published_at=post.published_at,
            source_name=CHANNEL_USERNAME,
            source_url=f"https://t.me/{CHANNEL_USERNAME}/{post.post_id}",
            content_hash=str(post.post_id),
        )
        for mention in extractor.extract(document):
            if mention.entity_type is not EntityType.PERSON:
                continue
            key = name_key(normalizer.normalize(mention, document).normalized_text)
            if key is not None:
                keys.add(key)
    return keys
