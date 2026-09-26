from datetime import datetime

from selectolax.parser import HTMLParser, Node

from sources.ingestion_errors import NoTextError, ParseError
from sources.models import ParsedArticle, RawDocument

TITLE_MAX_CHARS = 120
_REPLY = "tgme_widget_message_reply"


def own_text_node(root: HTMLParser | Node) -> Node | None:
    """The post's own text block. A reply shows the post it answers above it, in a text
    block of the same class inside the reply: that one is someone else's text."""
    for node in root.css(".tgme_widget_message_text"):
        parent = node.parent
        while parent is not None and _REPLY not in (parent.attributes.get("class") or ""):
            parent = parent.parent
        if parent is None:
            return node
    return None


class TelegramPostParser:
    """A single post from its embed view (https://t.me/<username>/<id>?embed=1)."""

    def parse(self, raw: RawDocument) -> ParsedArticle:
        # Line breaks inside a post are <br/>; keep them as paragraph boundaries.
        tree = HTMLParser(raw.content.replace(b"<br/>", b"\n").replace(b"<br>", b"\n"))
        text_node = own_text_node(tree)
        if text_node is None:
            # A rendered post (its bubble is there) without a text is a media post;
            # no bubble at all means the embed changed, a real failure.
            if tree.css_first(".tgme_widget_message_bubble") is not None:
                raise NoTextError("Post has no text")
            raise ParseError("Post text not found")
        lines = [" ".join(line.split()) for line in text_node.text().splitlines()]
        text = "\n".join(line for line in lines if line)
        if not text:
            raise NoTextError("Post text is empty")

        published_at = None
        time_node = tree.css_first(".tgme_widget_message_date time[datetime]")
        if time_node is not None and time_node.attributes.get("datetime"):
            published_at = datetime.fromisoformat(time_node.attributes["datetime"] or "")

        first_line = text.split("\n", 1)[0]
        title = (
            first_line
            if len(first_line) <= TITLE_MAX_CHARS
            else first_line[:TITLE_MAX_CHARS].rstrip() + "…"
        )
        return ParsedArticle(
            external_id=raw.external_id,
            url=raw.url,
            title=title,
            published_at=published_at,
            text=text,
        )
