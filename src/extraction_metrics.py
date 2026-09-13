from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

from extraction_documents import build_extraction_document_from_parsed_article
from extraction_events import RuleBasedEventExtractor
from extraction_extractors import RuleBasedEntityExtractor
from extraction_models import (
    ArticleExtractionResult,
    EntityType,
    EventType,
    ExtractionDocument,
    ExtractionRunStatus,
    ExtractionSaveResult,
)
from extraction_normalizers import RuleBasedMentionNormalizer
from extraction_pipeline import ExtractionPipeline


class GoldenMention(BaseModel):
    entity_type: EntityType
    surface_text: str
    start_offset: int | None = None
    end_offset: int | None = None
    occurrence: int = Field(default=1, ge=1)
    normalized_text: str


class GoldenEvent(BaseModel):
    event_type: EventType
    surface_text: str
    start_offset: int | None = None
    end_offset: int | None = None
    occurrence: int = Field(default=1, ge=1)


class GoldenArticle(BaseModel):
    article_id: int
    source_name: str
    source_url: str
    title: str
    text: str
    mentions: list[GoldenMention] = Field(default_factory=list)
    events: list[GoldenEvent] = Field(default_factory=list)

    @model_validator(mode="after")
    def fill_offsets(self) -> GoldenArticle:
        for mention in self.mentions:
            if mention.start_offset is None or mention.end_offset is None:
                mention.start_offset = _find_occurrence(
                    self.text,
                    mention.surface_text,
                    mention.occurrence,
                )
                mention.end_offset = mention.start_offset + len(mention.surface_text)
        for event in self.events:
            if event.start_offset is None or event.end_offset is None:
                event.start_offset = _find_occurrence(
                    self.text,
                    event.surface_text,
                    event.occurrence,
                )
                event.end_offset = event.start_offset + len(event.surface_text)
        return self

    def to_document(self) -> ExtractionDocument:
        from models import ParsedArticle

        return build_extraction_document_from_parsed_article(
            article_id=self.article_id,
            article=ParsedArticle(
                external_id=str(self.article_id),
                url=self.source_url,
                title=self.title,
                published_at=None,
                text=self.text,
            ),
            source_name=self.source_name,
            source_url=self.source_url,
        )


class ExtractionMetricReport(BaseModel):
    exact_span_precision: float
    exact_span_recall: float
    exact_span_f1: float
    entity_type_accuracy: float
    normalization_accuracy: float
    event_type_accuracy: float
    false_positive_mentions: list[str]
    missed_mentions: list[str]
    wrong_type_mentions: list[str]
    wrong_normalization_mentions: list[str]
    event_errors: list[str]


type MentionKey = tuple[int, int, int, str, str]
type SpanKey = tuple[int, int, int]
type EventKey = tuple[int, int, int, str]


class InMemoryExtractionPersistence:
    def __init__(self) -> None:
        self.results: list[ArticleExtractionResult] = []

    def save(self, result: ArticleExtractionResult) -> ExtractionSaveResult:
        self.results.append(result)

        return ExtractionSaveResult(
            run_id=len(self.results),
            article_id=result.document.article_id,
            status=ExtractionRunStatus.SUCCEEDED,
            mentions_created=len(result.mentions),
            events_created=len(result.events),
        )

    def save_failed(
        self,
        document: ExtractionDocument,
        *,
        extractor_name: str,
        extractor_version: str,
        normalizer_version: str,
        error_message: str,
    ) -> ExtractionSaveResult:
        return ExtractionSaveResult(
            run_id=0,
            article_id=document.article_id,
            status=ExtractionRunStatus.FAILED,
            error_message=error_message,
        )


def load_golden_articles(path: Path) -> list[GoldenArticle]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [GoldenArticle.model_validate(item) for item in data["articles"]]


def evaluate_golden_dataset(path: Path) -> ExtractionMetricReport:
    articles = load_golden_articles(path)
    persistence = InMemoryExtractionPersistence()
    pipeline = ExtractionPipeline(
        extractors=[RuleBasedEntityExtractor()],
        normalizers=[RuleBasedMentionNormalizer()],
        event_extractor=RuleBasedEventExtractor(),
        persistence=persistence,
    )
    for article in articles:
        pipeline.run(article.to_document())
    return _build_report(articles, persistence.results)


def _build_report(
    articles: list[GoldenArticle],
    results: list[ArticleExtractionResult],
) -> ExtractionMetricReport:
    expected_mentions: list[MentionKey] = []
    actual_mentions: list[MentionKey] = []
    for article, result in zip(articles, results, strict=True):
        expected_mentions.extend(
            (
                article.article_id,
                _require_offset(mention.start_offset),
                _require_offset(mention.end_offset),
                mention.entity_type.value,
                mention.normalized_text,
            )
            for mention in article.mentions
        )
        actual_mentions.extend(
            (
                article.article_id,
                mention.start_offset,
                mention.end_offset,
                mention.entity_type.value,
                mention.normalized_text,
            )
            for mention in result.mentions
        )
    expected_spans: set[SpanKey] = {(item[0], item[1], item[2]) for item in expected_mentions}
    actual_spans: set[SpanKey] = {(item[0], item[1], item[2]) for item in actual_mentions}
    true_positive_spans = expected_spans & actual_spans
    precision = _safe_div(len(true_positive_spans), len(actual_spans))
    recall = _safe_div(len(true_positive_spans), len(expected_spans))
    f1 = _safe_div(2 * precision * recall, precision + recall)
    actual_by_span = {(item[0], item[1], item[2]): item for item in actual_mentions}
    expected_by_span = {(item[0], item[1], item[2]): item for item in expected_mentions}
    type_matches = sum(
        1 for span in true_positive_spans if actual_by_span[span][3] == expected_by_span[span][3]
    )
    normalization_matches = sum(
        1 for span in true_positive_spans if actual_by_span[span][4] == expected_by_span[span][4]
    )
    expected_events: list[EventKey] = [
        (
            article.article_id,
            _require_offset(event.start_offset),
            _require_offset(event.end_offset),
            event.event_type.value,
        )
        for article in articles
        for event in article.events
    ]
    actual_events: list[EventKey] = [
        (result.document.article_id, event.start_offset, event.end_offset, event.event_type.value)
        for result in results
        for event in result.events
    ]
    event_matches = len(set(expected_events) & set(actual_events))
    return ExtractionMetricReport(
        exact_span_precision=precision,
        exact_span_recall=recall,
        exact_span_f1=f1,
        entity_type_accuracy=_safe_div(type_matches, len(true_positive_spans)),
        normalization_accuracy=_safe_div(normalization_matches, len(true_positive_spans)),
        event_type_accuracy=_safe_div(event_matches, len(expected_events)),
        false_positive_mentions=[str(item) for item in sorted(actual_spans - expected_spans)],
        missed_mentions=[str(item) for item in sorted(expected_spans - actual_spans)],
        wrong_type_mentions=[
            str(span)
            for span in sorted(true_positive_spans)
            if actual_by_span[span][3] != expected_by_span[span][3]
        ],
        wrong_normalization_mentions=[
            str(span)
            for span in sorted(true_positive_spans)
            if actual_by_span[span][4] != expected_by_span[span][4]
        ],
        event_errors=[str(item) for item in sorted(set(expected_events) - set(actual_events))],
    )


def _safe_div(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 1.0
    return numerator / denominator


def _find_occurrence(text: str, surface_text: str, occurrence: int) -> int:
    start = -1
    search_from = 0
    for _ in range(occurrence):
        start = text.find(surface_text, search_from)
        if start == -1:
            raise ValueError(f"surface_text not found in golden article: {surface_text}")
        search_from = start + len(surface_text)
    return start


def _require_offset(value: int | None) -> int:
    if value is None:
        raise ValueError("golden offset was not populated")
    return value
