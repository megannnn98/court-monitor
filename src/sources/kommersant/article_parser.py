"""A Kommersant news page (`kommersant.ru/doc/<id>`).

The site's own Telegram channel often leaves a defendant unnamed where the page names
him, citing the prosecutor: «27-летнего жителя Советского района Мамута Белялова».
"""

from __future__ import annotations

import json
import re
from datetime import datetime

from selectolax.parser import HTMLParser

from sources.ingestion_errors import ParseError
from sources.models import ParsedArticle, RawDocument
from sources.rss.source_adapter import RssItem

KOMMERSANT_FEED_URL = "https://www.kommersant.ru/rss/news.xml"
# Sections that never hold a Russian persecution case.
_SKIPPED_SECTIONS = frozenset({"Мир", "Спорт", "Бизнес", "Финансы", "Культура", "Стиль"})
# A court, a detention or a criminal case in the title or the lead.
_CASE_WORDS = re.compile(
    r"(?<![а-яё])(?:суд|приговор|осужд|арест|задерж|сизо|колони|обвин|уголовн|госизмен|"
    r"измен[аеуы](?![а-яё])|экстремис|террор|фейк|дискредит|иноагент|нежелательн|обыск|"
    r"преследован)",
    re.IGNORECASE,
)
_DOC_ID = re.compile(r"/doc/(\d+)")


def is_case_news(item: RssItem) -> bool:
    return item.category not in _SKIPPED_SECTIONS and bool(
        _CASE_WORDS.search(f"{item.title} {item.description}")
    )


def kommersant_external_id(link: str) -> str:
    match = _DOC_ID.search(link)
    return match.group(1) if match else link


class KommersantArticleParser:
    def parse(self, raw: RawDocument) -> ParsedArticle:
        tree = HTMLParser(raw.content)
        title_node = tree.css_first("h1")
        paragraphs = [" ".join(node.text().split()) for node in tree.css("p.doc__text")]
        paragraphs = [paragraph for paragraph in paragraphs if paragraph]
        if title_node is None:
            raise ParseError("Title not found")
        if not paragraphs:
            raise ParseError("Article text not found")
        return ParsedArticle(
            external_id=raw.external_id,
            url=raw.url,
            title=" ".join(title_node.text().split()),
            published_at=_published_at(tree),
            text="\n".join(paragraphs),
        )


def _published_at(tree: HTMLParser) -> datetime | None:
    for node in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(node.text())
        except ValueError:
            continue
        for item in data if isinstance(data, list) else [data]:
            value = item.get("datePublished") if isinstance(item, dict) else None
            if isinstance(value, str):
                try:
                    return datetime.fromisoformat(value)
                except ValueError:
                    continue
    return None
