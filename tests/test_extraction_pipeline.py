import pytest

from extraction_events import RuleBasedEventExtractor
from extraction_models import (
    ArticleExtractionResult,
    EntityType,
    ExtractionDocument,
    ExtractionRunStatus,
    ExtractionSaveResult,
    NormalizedMention,
    PersonNormalizedData,
    RawMention,
)
from extraction_pipeline import ExtractionPipeline


class FakeExtractor:
    extractor_name = "fake"
    extractor_version = "1"

    def __init__(self) -> None:
        self.calls = 0

    def extract(self, document: ExtractionDocument) -> list[RawMention]:
        self.calls += 1
        start = document.text.index("Иван")
        return [
            RawMention(
                entity_type=EntityType.PERSON,
                surface_text="Иван",
                start_offset=start,
                end_offset=start + 4,
                confidence=0.9,
                extractor_name=self.extractor_name,
                extractor_version=self.extractor_version,
            ),
            RawMention(
                entity_type=EntityType.PERSON,
                surface_text="Иван",
                start_offset=start,
                end_offset=start + 4,
                confidence=0.9,
                extractor_name=self.extractor_name,
                extractor_version=self.extractor_version,
            ),
        ]


class FakeNormalizer:
    normalizer_version = "1"

    def supports(self, entity_type: EntityType) -> bool:
        return entity_type is EntityType.PERSON

    def normalize(
        self,
        mention: RawMention,
        document: ExtractionDocument,
    ) -> NormalizedMention:
        return NormalizedMention(
            entity_type=mention.entity_type,
            surface_text=mention.surface_text,
            normalized_text="Иван",
            start_offset=mention.start_offset,
            end_offset=mention.end_offset,
            confidence=mention.confidence,
            normalized_data=PersonNormalizedData(full_name="Иван", matching_key="иван"),
            extractor_name=mention.extractor_name,
            extractor_version=mention.extractor_version,
            normalizer_version=self.normalizer_version,
        )


class FakePersistence:
    def __init__(self) -> None:
        self.result: ArticleExtractionResult | None = None
        self.failed: str | None = None

    def save(self, result: ArticleExtractionResult) -> ExtractionSaveResult:
        self.result = result
        return ExtractionSaveResult(
            run_id=1,
            article_id=result.document.article_id,
            status=ExtractionRunStatus.SUCCEEDED,
            mentions_created=len(result.mentions),
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
        self.failed = error_message
        return ExtractionSaveResult(
            run_id=2,
            article_id=document.article_id,
            status=ExtractionRunStatus.FAILED,
            error_message=error_message,
        )


def make_document() -> ExtractionDocument:
    return ExtractionDocument(
        article_id=1,
        title="Test",
        text="Иван пришел.",
        published_at=None,
        source_name="ОВД-Инфо",
        source_url="https://ovd.info/test",
        content_hash="abc",
    )


def test_pipeline_validates_normalizes_deduplicates_and_persists() -> None:
    extractor = FakeExtractor()
    persistence = FakePersistence()
    pipeline = ExtractionPipeline(
        extractors=[extractor],
        normalizers=[FakeNormalizer()],
        event_extractor=RuleBasedEventExtractor(),
        persistence=persistence,
    )

    result = pipeline.run(make_document())

    assert extractor.calls == 1
    assert result.status is ExtractionRunStatus.SUCCEEDED
    assert persistence.result is not None
    assert len(persistence.result.mentions) == 1


def test_pipeline_saves_failed_run_on_invalid_span() -> None:
    class BrokenExtractor(FakeExtractor):
        def extract(self, document: ExtractionDocument) -> list[RawMention]:
            return [
                RawMention(
                    entity_type=EntityType.PERSON,
                    surface_text="Петр",
                    start_offset=0,
                    end_offset=4,
                    confidence=0.9,
                )
            ]

    persistence = FakePersistence()
    pipeline = ExtractionPipeline(
        extractors=[BrokenExtractor()],
        normalizers=[FakeNormalizer()],
        event_extractor=RuleBasedEventExtractor(),
        persistence=persistence,
    )

    result = pipeline.run(make_document())

    assert result.status is ExtractionRunStatus.FAILED
    assert "span does not match" in (persistence.failed or "")


def test_pipeline_does_not_convert_programmer_error_to_failed_run() -> None:
    class BrokenNormalizer(FakeNormalizer):
        def normalize(
            self,
            mention: RawMention,
            document: ExtractionDocument,
        ) -> NormalizedMention:
            raise RuntimeError("programmer bug")

    persistence = FakePersistence()
    pipeline = ExtractionPipeline(
        extractors=[FakeExtractor()],
        normalizers=[BrokenNormalizer()],
        event_extractor=RuleBasedEventExtractor(),
        persistence=persistence,
    )

    with pytest.raises(RuntimeError, match="programmer bug"):
        pipeline.run(make_document())
    assert persistence.failed is None
