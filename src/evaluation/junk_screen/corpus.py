"""The articles of a database from before the purge, judged by today's extraction.

For each article: would `purge-junk` keep it (a criminal-case event in today's rule-based
extraction) and does its text carry legal words at all — the pool where a missed event
hides. Nothing is written to the database it reads.
"""

from __future__ import annotations

import json
import random
import re
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

from extraction.documents import content_hash_for_text
from extraction.events import RuleBasedEventExtractor
from extraction.models import ArticleExtractionResult, ExtractionDocument
from extraction.normalizers import RuleBasedMentionNormalizer
from extraction.person_ner.factory import build_entity_extractor
from extraction.pipeline import ExtractionPipeline
from monitoring.junk_purge import CRIMINAL_EVENT_TYPES

# Words of a criminal case, crude on purpose: an article without any of them is not where
# the extraction misses a case.
LEGAL_WORDS = re.compile(
    r"задерж|арест|приговор|осуд|обвин|возбуд|уголовн|\bУК\b|обыск|СИЗО|колони|"
    r"подозрева|розыск|суд",
    re.IGNORECASE,
)

_ARTICLES = text(
    """
    SELECT a.id, a.title, a.text, a.published_at, s.name AS source, d.canonical_url
    FROM parsed_articles a
    JOIN source_documents d ON d.id = a.document_id
    JOIN sources s ON s.id = d.source_id
    WHERE coalesce(a.text, '') <> ''
    ORDER BY a.id
    """
)


@dataclass(frozen=True)
class JudgedArticle:
    article_id: int
    source: str
    url: str
    published_at: str | None
    title: str
    # Today's purge keeps it: a criminal-case event in today's extraction.
    rule_keeps: bool
    events: tuple[str, ...]
    legal_words: bool
    length: int


def _value(kind: Any) -> str:
    return str(getattr(kind, "value", kind))


class _Captured:
    """Persistence that keeps nothing: the pipeline's result is all that is wanted."""

    def __init__(self) -> None:
        self.result: ArticleExtractionResult | None = None

    def save(self, result: ArticleExtractionResult) -> Any:
        self.result = result
        return None

    def save_failed(self, document: ExtractionDocument, **_kwargs: Any) -> Any:
        self.result = None
        return None


def judged_articles(database_url: str) -> Iterator[tuple[JudgedArticle, str]]:
    """Each article judged, with its text."""
    captured = _Captured()
    pipeline = ExtractionPipeline(
        extractors=[build_entity_extractor()],
        normalizers=[RuleBasedMentionNormalizer()],
        event_extractor=RuleBasedEventExtractor(),
        persistence=captured,
    )
    engine = create_engine(database_url)
    with engine.connect() as connection:
        for row in connection.execute(_ARTICLES):
            document = ExtractionDocument(
                article_id=row.id,
                title=row.title or "",
                text=row.text,
                published_at=row.published_at,
                source_name=row.source,
                source_url=row.canonical_url,
                content_hash=content_hash_for_text(row.text),
            )
            pipeline.run(document)
            events = (
                tuple(sorted({_value(event.event_type) for event in captured.result.events}))
                if captured.result is not None
                else ()
            )
            yield (
                JudgedArticle(
                    article_id=row.id,
                    source=row.source,
                    url=row.canonical_url,
                    published_at=row.published_at.isoformat() if row.published_at else None,
                    title=row.title or "",
                    rule_keeps=any(event in CRIMINAL_EVENT_TYPES for event in events),
                    events=events,
                    legal_words=bool(LEGAL_WORDS.search(f"{row.title}\n{row.text}")),
                    length=len(row.text),
                ),
                row.text,
            )


def write_corpus(database_url: str, path: Path) -> dict[str, int]:
    """JSON lines of judged articles with their text; counts by outcome."""
    path.parent.mkdir(parents=True, exist_ok=True)
    counts = {"articles": 0, "rule_keeps": 0, "purged_with_legal_words": 0}
    with path.open("w", encoding="utf-8") as out:
        for article, body in judged_articles(database_url):
            counts["articles"] += 1
            counts["rule_keeps"] += article.rule_keeps
            counts["purged_with_legal_words"] += (not article.rule_keeps) and article.legal_words
            out.write(json.dumps({**asdict(article), "text": body}, ensure_ascii=False) + "\n")
    return counts


# The labelled sample: mostly the purged articles with legal words, where a missed case
# hides; some purged without, to see that little hides there; some kept, for the rules.
SAMPLE_SIZES = {"purged_legal": 1400, "purged_plain": 250, "kept": 350}
SAMPLE_SEED = 20260927


def write_sample(corpus: Path, path: Path) -> dict[str, int]:
    """A seeded sample of the judged corpus by stratum, with the texts, as JSON lines."""
    rows = [json.loads(line) for line in corpus.read_text(encoding="utf-8").splitlines()]
    pools: dict[str, list[dict[str, Any]]] = {
        "purged_legal": [r for r in rows if not r["rule_keeps"] and r["legal_words"]],
        "purged_plain": [r for r in rows if not r["rule_keeps"] and not r["legal_words"]],
        "kept": [r for r in rows if r["rule_keeps"]],
    }
    chosen = random.Random(SAMPLE_SEED)
    with path.open("w", encoding="utf-8") as out:
        for stratum, size in SAMPLE_SIZES.items():
            for row in chosen.sample(pools[stratum], size):
                out.write(json.dumps({**row, "stratum": stratum}, ensure_ascii=False) + "\n")
    return {stratum: len(pool) for stratum, pool in pools.items()}
