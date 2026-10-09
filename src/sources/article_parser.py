from datetime import datetime
from zoneinfo import ZoneInfo

from selectolax.parser import HTMLParser, Node

from monitor_core.errors import ParseError
from monitor_core.model import ParsedArticle, RawDocument

# The parser port lives in `monitor_core.ports`; re-exported for existing imports.
from monitor_core.ports import ArticleParser

__all__ = [
    "OVD_INFO_TIMEZONE",
    "ArticleParser",
    "OvdInfoArticleParser",
]


OVD_INFO_TIMEZONE = ZoneInfo("Europe/Moscow")


def _extract_text(node: Node) -> str:
    return " ".join(node.text().split())


class OvdInfoArticleParser:
    TITLE_SELECTOR = "h1.express-text-heading"
    PUBLISHED_SELECTOR = "#article_published"
    PARAGRAPH_SELECTOR = ".field--name-field-express-text > p"
    # Daily digests list their stories as `<ul><li>` instead of paragraphs.
    LIST_ITEM_SELECTOR = ".field--name-field-express-text > ul > li"

    def parse(self, raw: RawDocument) -> ParsedArticle:
        tree = HTMLParser(raw.content)

        title_node = tree.css_first(self.TITLE_SELECTOR)
        published_node = tree.css_first(self.PUBLISHED_SELECTOR)
        paragraph_nodes = self._paragraph_nodes(tree)

        if title_node is None:
            raise ParseError("Title not found")

        if not paragraph_nodes:
            raise ParseError("Paragraphs not found")

        published_at = None

        if published_node is not None:
            date_text = _extract_text(published_node)
            published_at = datetime.strptime(
                date_text,
                "%d.%m.%Y, %H:%M",
            ).replace(tzinfo=OVD_INFO_TIMEZONE)

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

    def _paragraph_nodes(self, tree: HTMLParser) -> list[Node]:
        """Paragraphs and digest list items, in document order."""
        wanted = {
            node.mem_id
            for node in [*tree.css(self.PARAGRAPH_SELECTOR), *tree.css(self.LIST_ITEM_SELECTOR)]
        }
        if tree.root is None or not wanted:
            return []
        return [node for node in tree.root.traverse() if node.mem_id in wanted]
