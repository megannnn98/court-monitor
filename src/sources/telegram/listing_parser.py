"""Posts of a channel's public web preview page (https://t.me/s/<username>)."""

from dataclasses import dataclass
from datetime import datetime

from selectolax.parser import HTMLParser

from sources.models import SourceReference
from sources.telegram.article_parser import own_text_node


@dataclass(frozen=True)
class TelegramListingPost:
    post_id: int
    published_at: datetime
    has_text: bool
    reference: SourceReference


def post_reference(username: str, post_id: int) -> SourceReference:
    # The embed view carries the post text and its date; it also opens the post in a browser.
    return SourceReference(
        external_id=str(post_id),
        url=f"https://t.me/{username}/{post_id}?embed=1&mode=tme",
    )


class TelegramListingParser:
    def __init__(self, username: str) -> None:
        self._username = username

    def parse(self, html: bytes) -> list[TelegramListingPost]:
        """Posts of this channel on the page, newest first."""
        posts: list[TelegramListingPost] = []
        for node in HTMLParser(html).css("div.tgme_widget_message[data-post]"):
            channel, _, raw_id = (node.attributes.get("data-post") or "").partition("/")
            time_node = node.css_first(".tgme_widget_message_date time[datetime]")
            if channel.lower() != self._username.lower() or not raw_id.isdigit() or not time_node:
                continue
            post_id = int(raw_id)
            posts.append(
                TelegramListingPost(
                    post_id=post_id,
                    published_at=datetime.fromisoformat(time_node.attributes["datetime"] or ""),
                    has_text=own_text_node(node) is not None,
                    reference=post_reference(self._username, post_id),
                )
            )
        return sorted(posts, key=lambda post: post.post_id, reverse=True)
