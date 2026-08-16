from datetime import datetime

from selectolax.parser import HTMLParser

from models import ParsedArticle, RawDocument


def _extract_text(node) -> str:
    return " ".join(node.text().split())


class OvdInfoArticleParser:
    TITLE_SELECTOR = "h1.express-text-heading"
    PUBLISHED_SELECTOR = "#article_published"
    PARAGRAPH_SELECTOR = ".field--name-field-express-text > p"

    def parse(self, raw: RawDocument) -> ParsedArticle:
        tree = HTMLParser(raw.content)

        title_node = tree.css_first(self.TITLE_SELECTOR)
        published_node = tree.css_first(self.PUBLISHED_SELECTOR)
        paragraph_nodes = tree.css(self.PARAGRAPH_SELECTOR)

        if title_node is None:
            raise ValueError("Title not found")

        if not paragraph_nodes:
            raise ValueError("Paragraphs not found")

        published_at = None

        if published_node is not None:
            date_text = _extract_text(published_node)
            published_at = datetime.strptime(
                date_text,
                "%d.%m.%Y, %H:%M",
            )

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
