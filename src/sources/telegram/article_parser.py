from datetime import datetime

from selectolax.parser import HTMLParser

from sources.ingestion_errors import ParseError
from sources.models import ParsedArticle, RawDocument

TITLE_MAX_CHARS = 120


class TelegramPostParser:
    """A single post from its embed view (https://t.me/<username>/<id>?embed=1)."""

    def parse(self, raw: RawDocument) -> ParsedArticle:
        # Line breaks inside a post are <br/>; keep them as paragraph boundaries.
        tree = HTMLParser(raw.content.replace(b"<br/>", b"\n").replace(b"<br>", b"\n"))
        text_node = tree.css_first(".tgme_widget_message_text")
        if text_node is None:
            raise ParseError("Post text not found")
        lines = [" ".join(line.split()) for line in text_node.text().splitlines()]
        text = "\n".join(line for line in lines if line)
        if not text:
            raise ParseError("Post text is empty")

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
