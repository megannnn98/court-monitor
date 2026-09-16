import re
from datetime import datetime
from zoneinfo import ZoneInfo

from selectolax.parser import HTMLParser, Node

from sources.ingestion_errors import ParseError
from sources.models import ParsedArticle, RawDocument

SUDRF_TIMEZONE = ZoneInfo("Europe/Moscow")
_DATE_PATTERN = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")


def _extract_text(node: Node) -> str:
    return " ".join(node.text().split())


class SudrfArticleParser:
    TITLE_SELECTOR = "#tdNewsDetailedTitle"
    # Holds "Новость от 07.08.2026" above the news body.
    PUBLISHED_SELECTOR = "#divModuleSubTitle"
    PARAGRAPH_SELECTOR = "td.printVersionBody p"

    def parse(self, raw: RawDocument) -> ParsedArticle:
        # The pages are windows-1251; selectolax honours their charset meta tag.
        tree = HTMLParser(raw.content)

        title_node = tree.css_first(self.TITLE_SELECTOR)

        if title_node is None:
            raise ParseError("Title not found")

        title = _extract_text(title_node)

        paragraph_texts = [
            text for node in tree.css(self.PARAGRAPH_SELECTOR) if (text := _extract_text(node))
        ]

        # The body opens by repeating the headline; keeping it makes the event
        # extractor read the same verdict twice, once from the headline sentence.
        if paragraph_texts and paragraph_texts[0] == title:
            del paragraph_texts[0]

        if not paragraph_texts:
            raise ParseError("Paragraphs not found")

        published_at = None
        published_node = tree.css_first(self.PUBLISHED_SELECTOR)

        if published_node is not None:
            match = _DATE_PATTERN.search(_extract_text(published_node))

            if match is not None:
                day, month, year = (int(part) for part in match.groups())
                published_at = datetime(year, month, day, tzinfo=SUDRF_TIMEZONE)

        return ParsedArticle(
            external_id=raw.external_id,
            url=raw.url,
            title=title,
            published_at=published_at,
            text="\n\n".join(paragraph_texts),
        )
