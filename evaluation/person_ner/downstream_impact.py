"""What the person-extraction strategy does to entity resolution, on a disposable database.

A better person recall is not worth having if it floods entity resolution with ambiguous
surname-only mentions and invents canonical people. This runs the same articles through
extraction and resolution once per strategy and compares what came out.

    EVALUATION_DATABASE_URL=… uv run python evaluation/person_ner/downstream_impact.py
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from db.database import create_database_engine, create_session_factory
from db.maintenance import require_disposable_database, truncate_disposable_tables
from db.orm_models import (
    EntityMentionRecord,
    ParsedArticleRecord,
    PersonRecord,
    PersonResolutionDecisionRecord,
    Source,
    SourceDocument,
)
from extraction.documents import build_extraction_document_from_parsed_article
from extraction.events import RuleBasedEventExtractor
from extraction.normalizers import RuleBasedMentionNormalizer
from extraction.parallel_resolution import resolve_runs
from extraction.persistence import SqlAlchemyExtractionPersistence
from extraction.person_ner.config import PersonExtractionStrategy, PersonNerSettings
from extraction.person_ner.factory import build_entity_extractor
from extraction.pipeline import ExtractionPipeline
from sources.models import ParsedArticle

COMPARISON = Path("var/person_ner/comparison.json")


def load_texts() -> dict[str, str]:
    data = json.loads(COMPARISON.read_text(encoding="utf-8"))
    return dict(data["texts"])


def seed(session_factory: Any, texts: dict[str, str]) -> list[int]:
    """One source, one document and one parsed article per text."""
    article_ids: list[int] = []
    with session_factory.begin() as session:
        source = Source(name="ОВД-Инфо", base_url="https://ovd.info")
        session.add(source)
        session.flush()
        for case_id, text in texts.items():
            document = SourceDocument(
                source_id=source.id,
                external_id=case_id,
                canonical_url=f"https://ovd.info/{case_id}",
                fetched_at=func.now(),
                content_type="text/html",
                raw_content=b"",
            )
            session.add(document)
            session.flush()
            article = ParsedArticleRecord(document_id=document.id, title="", text=text)
            session.add(article)
            session.flush()
            article_ids.append(article.id)
    return article_ids


def run_strategy(
    database_url: str,
    texts: dict[str, str],
    strategy: PersonExtractionStrategy,
    recognizer: Any,
) -> dict[str, Any]:
    engine = create_database_engine(database_url)
    require_disposable_database(engine)
    truncate_disposable_tables(engine)
    session_factory = create_session_factory(engine)

    article_ids = seed(session_factory, texts)
    settings = PersonNerSettings.from_env({"PERSON_EXTRACTION_STRATEGY": strategy.value})
    extractor = build_entity_extractor(settings, recognizer=recognizer)
    pipeline = ExtractionPipeline(
        extractors=[extractor],
        normalizers=[RuleBasedMentionNormalizer()],
        event_extractor=RuleBasedEventExtractor(),
        persistence=SqlAlchemyExtractionPersistence(session_factory),
    )

    started = time.monotonic()
    run_ids: list[int] = []
    with session_factory() as session:
        rows = session.execute(
            select(ParsedArticleRecord, Source, SourceDocument)
            .join(SourceDocument, ParsedArticleRecord.document_id == SourceDocument.id)
            .join(Source, SourceDocument.source_id == Source.id)
            .where(ParsedArticleRecord.id.in_(article_ids))
        ).all()
        documents = [
            build_extraction_document_from_parsed_article(
                article_id=article.id,
                article=ParsedArticle(
                    external_id=document.external_id,
                    url=document.canonical_url,
                    title=article.title,
                    published_at=article.published_at,
                    text=article.text,
                ),
                source_name=source.name,
                source_url=source.base_url,
            )
            for article, source, document in rows
        ]
    for document in documents:
        run_ids.append(pipeline.run(document).run_id)
    extract_seconds = time.monotonic() - started

    stats = resolve_runs(database_url, run_ids, workers=1)

    with session_factory() as session:
        person_mentions = session.scalar(
            select(func.count(EntityMentionRecord.id)).where(
                EntityMentionRecord.entity_type == "person"
            )
        )
        linked = session.scalar(
            select(func.count(EntityMentionRecord.id)).where(
                EntityMentionRecord.entity_type == "person",
                EntityMentionRecord.person_id.is_not(None),
            )
        )
        persons = session.scalar(select(func.count(PersonRecord.id)))
        actions = dict(
            session.execute(
                select(
                    PersonResolutionDecisionRecord.action,
                    func.count(PersonResolutionDecisionRecord.id),
                ).group_by(PersonResolutionDecisionRecord.action)
            ).all()
        )
        statuses = dict(
            session.execute(
                select(
                    PersonResolutionDecisionRecord.status,
                    func.count(PersonResolutionDecisionRecord.id),
                ).group_by(PersonResolutionDecisionRecord.status)
            ).all()
        )

    engine.dispose()
    return {
        "strategy": strategy.value,
        "extractor_version": extractor.extractor_version,
        "extract_seconds": round(extract_seconds, 2),
        "person_mentions": person_mentions,
        "person_mentions_linked": linked,
        "persons": persons,
        "decision_actions": actions,
        "decision_statuses": statuses,
        "mentions_resolved": stats.mentions_resolved,
        "new_persons_created": stats.new_persons_created,
        "reviews_pending": stats.reviews_pending,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("EVALUATION_DATABASE_URL"))
    parser.add_argument("--device", default="cuda")
    args = parser.parse_args()
    if not args.database_url:
        raise SystemExit("EVALUATION_DATABASE_URL is not set")

    texts = load_texts()
    print(f"articles={len(texts)}")

    from extraction.person_ner.gliner_recognizer import GlinerPersonNameRecognizer

    settings = PersonNerSettings.from_env({})
    recognizer = GlinerPersonNameRecognizer(
        settings.model_id, device=args.device, min_score=settings.min_score
    )

    report = {}
    for strategy in (
        PersonExtractionStrategy.RULE_BASED,
        PersonExtractionStrategy.NER,
        PersonExtractionStrategy.HYBRID,
    ):
        result = run_strategy(args.database_url, texts, strategy, recognizer)
        report[strategy.value] = result
        print(json.dumps(result, ensure_ascii=False))

    out = Path("var/person_ner/downstream.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"report: {out}")


if __name__ == "__main__":
    main()
