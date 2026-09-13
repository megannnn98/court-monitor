from datetime import UTC, datetime

from article_parser import OvdInfoArticleParser
from extraction_documents import build_extraction_document_from_parsed_article
from extraction_events import RuleBasedEventExtractor
from extraction_extractors import RuleBasedEntityExtractor
from extraction_metrics import InMemoryExtractionPersistence
from extraction_models import EntityType, EventType, ExtractionRunStatus
from extraction_normalizers import RuleBasedMentionNormalizer
from extraction_pipeline import ExtractionPipeline
from models import RawDocument


def test_html_fixture_to_extraction_result() -> None:
    html = """
    <html>
      <body>
        <h1 class="express-text-heading">Test article</h1>
        <div id="article_published">13.09.2026, 12:30</div>
        <div class="field--name-field-express-text">
          <p>Басманный районный суд Москвы арестовал Александра Иванова по ст. 207.3 УК РФ.</p>
        </div>
      </body>
    </html>
    """.encode()
    raw = RawDocument(
        external_id="/test",
        url="https://ovd.info/test",
        fetched_at=datetime(2026, 9, 13, tzinfo=UTC),
        content_type="text/html",
        content=html,
    )
    article = OvdInfoArticleParser().parse(raw)
    document = build_extraction_document_from_parsed_article(
        article_id=1,
        article=article,
        source_name="ОВД-Инфо",
        source_url=article.url,
    )
    persistence = InMemoryExtractionPersistence()
    pipeline = ExtractionPipeline(
        extractors=[RuleBasedEntityExtractor()],
        normalizers=[RuleBasedMentionNormalizer()],
        event_extractor=RuleBasedEventExtractor(),
        persistence=persistence,
    )

    save_result = pipeline.run(document)

    assert save_result.status is ExtractionRunStatus.SUCCEEDED
    result = persistence.results[0]
    assert {mention.entity_type for mention in result.mentions} >= {
        EntityType.PERSON,
        EntityType.COURT,
        EntityType.LEGAL_REFERENCE,
    }
    assert [event.event_type for event in result.events] == [EventType.ARREST]
