import re
from collections.abc import Iterator
from datetime import datetime
from zoneinfo import ZoneInfo

from selectolax.parser import HTMLParser, Node

from monitor_core.errors import ParseError
from monitor_core.model import ParsedArticle, RawDocument

SUDRF_TIMEZONE = ZoneInfo("Europe/Moscow")
_DATE_PATTERN = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")


def _extract_text(node: Node) -> str:
    return " ".join(node.text().split())


# Marks a paragraph break in the stream `_body_parts` yields.
_BREAK = None
# Elements that start and end a paragraph of their own.
_BLOCK_TAGS = frozenset(
    {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "ul", "ol", "table", "tr", "td"}
)
_SKIPPED_TAGS = frozenset({"script", "style"})
# A blank line inside a text node: the only break plain text has.
_BLANK_LINE = re.compile(r"\n[ \t\xa0]*\n")


def _body_parts(node: Node) -> Iterator[str | None]:
    """The text of `node` in document order, with `_BREAK` where a paragraph ends: at a
    `<br>`, around a block element and at a blank line. A single newline is a line
    wrapped inside a paragraph, not a break."""
    for child in node.iter(include_text=True):
        if child.tag == "-text":
            for index, part in enumerate(_BLANK_LINE.split(child.text(deep=False))):
                if index:
                    yield _BREAK
                yield part
        elif child.tag == "br":
            yield _BREAK
        elif child.tag in _BLOCK_TAGS:
            yield _BREAK
            yield from _body_parts(child)
            yield _BREAK
        elif child.tag not in _SKIPPED_TAGS:
            yield from _body_parts(child)


def _body_paragraphs(body: Node, title: str) -> list[str]:
    """The paragraphs of a body whose text is not in `<p>`: pasted from VK (`<span>`s and
    `<br>`s), plain text in the cell, or a `<p>` for the headline and the rest beside it.
    The headline the body repeats is dropped when it stands alone: as the first
    paragraph, or as the first line of it."""
    raw: list[str] = []
    current: list[str] = []
    for part in _body_parts(body):
        if part is _BREAK:
            raw.append("".join(current))
            current = []
        else:
            current.append(part)
    raw.append("".join(current))
    paragraphs: list[str] = []
    for text in raw:
        lines = [line for line in text.split("\n") if line.strip()]
        if not paragraphs and lines and " ".join(lines[0].split()) == title:
            del lines[0]
        paragraph = " ".join(" ".join(lines).split())
        if paragraph and not (not paragraphs and paragraph == title):
            paragraphs.append(paragraph)
    return paragraphs


class SudrfArticleParser:
    TITLE_SELECTOR = "#tdNewsDetailedTitle"
    # Holds "Новость от 07.08.2026" above the news body.
    PUBLISHED_SELECTOR = "#divModuleSubTitle"
    BODY_SELECTOR = "td.printVersionBody"
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

        if not paragraph_texts and (body := tree.css_first(self.BODY_SELECTOR)) is not None:
            paragraph_texts = _body_paragraphs(body, title)

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
