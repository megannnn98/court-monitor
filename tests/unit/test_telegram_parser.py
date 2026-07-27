"""Tests: Telegram preview / single-post parsing on a saved fixture."""

from __future__ import annotations

from datetime import timezone
from pathlib import Path

from court_monitor.parsers.telegram_post import (
    parse_channel_preview,
    parse_single_post,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "telegram"
PREVIEW = FIXTURES / "tg_preview_extremizmunet.html"
SINGLE = FIXTURES / "tg_post_extremizmunet.html"


def test_parse_preview_returns_posts() -> None:
    posts = parse_channel_preview(PREVIEW.read_text(encoding="utf-8"))
    assert len(posts) >= 10
    first = posts[0]
    assert first.post_id is not None and first.post_id.isdigit()
    assert first.permalink and first.permalink.startswith("https://t.me/extremizmunet/")
    assert first.channel == "extremizmunet"
    assert first.published_at is not None
    assert first.published_at.tzinfo == timezone.utc
    assert first.text  # non-empty body


def test_preview_posts_are_unique() -> None:
    posts = parse_channel_preview(PREVIEW.read_text(encoding="utf-8"))
    ids = [p.post_id for p in posts if p.post_id]
    assert len(ids) == len(set(ids))


def test_parse_single_post() -> None:
    post = parse_single_post(SINGLE.read_text(encoding="utf-8"))
    assert post is not None
    assert post.post_id == "3987"
    assert post.permalink == "https://t.me/extremizmunet/3987"
    assert post.author == "Экстремизму - НЕТ!"
    assert "теракта" in post.text.lower() or "терроризм" in post.text.lower()


def test_parse_single_post_returns_none_on_empty() -> None:
    assert parse_single_post("<html><body>no post here</body></html>") is None
