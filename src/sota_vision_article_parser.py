import re
from datetime import datetime
from zoneinfo import ZoneInfo

from selectolax.parser import HTMLParser, Node

from ingestion_errors import ParseError
from models import ParsedArticle, RawDocument

SOTA_VISION_TIMEZONE = ZoneInfo("Europe/Moscow")
_DATE_PATTERN = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")


def _extract_text(node: Node) -> str:
    return " ".join(node.text().split())


class SotaVisionArticleParser:
    TITLE_SELECTOR = "h1.entry-title"
    PARAGRAPH_SELECTOR = "div.entry-content > p"
    DATE_SELECTOR = "footer.entry-footer .entry-date"

    def parse(self, raw: RawDocument) -> ParsedArticle:
        tree = HTMLParser(raw.content)

        title_node = tree.css_first(self.TITLE_SELECTOR)
        paragraph_nodes = tree.css(self.PARAGRAPH_SELECTOR)

        if title_node is None:
            raise ParseError("Title not found")

        if not paragraph_nodes:
            raise ParseError("Paragraphs not found")

        published_at = None
        date_node = tree.css_first(self.DATE_SELECTOR)

        if date_node is not None:
            match = _DATE_PATTERN.search(_extract_text(date_node))

            if match is not None:
                day, month, year = (int(part) for part in match.groups())
                published_at = datetime(year, month, day, tzinfo=SOTA_VISION_TIMEZONE)

        title = _extract_text(title_node)

        paragraph_texts = [_extract_text(node) for node in paragraph_nodes]

        text = "\n\n".join(paragraph_texts)

        return ParsedArticle(
            external_id=raw.external_id,
            url=raw.url,
            title=title,
            published_at=published_at,
            text=text,
        )
