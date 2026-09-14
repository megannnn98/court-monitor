"""In-memory upstream sources and wiring for monitoring pipeline tests (no Internet)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
from qdrant_client import QdrantClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from application import build_monitoring_service
from db.orm_models import Base
from extraction.events import RuleBasedEventExtractor
from extraction.extractors import RuleBasedEntityExtractor
from extraction.models import ExtractionDocument, RawMention
from extraction.normalizers import RuleBasedMentionNormalizer
from extraction.persistence import SqlAlchemyExtractionPersistence
from extraction.pipeline import ExtractionPipeline
from monitoring.models import MonitoringSettings
from monitoring.service import MonitoringService
from rosfinmonitoring.ingestion import RosfinmonitoringIngestionPipeline
from rosfinmonitoring.persistence import RosfinmonitoringPersistence
from semantic_retrieval.document_store import SqlAlchemySemanticDocumentRepository
from semantic_retrieval.documents import (
    EventSemanticDocumentBuilder,
    PersonSemanticDocumentBuilder,
)
from semantic_retrieval.indexer import SemanticIndexer
from semantic_retrieval.models import RetrievalEntityType
from semantic_retrieval.vector_store import QdrantVectorStore, VectorStore
from sources.ingestion_errors import ParseError
from sources.models import ParsedArticle, RawDocument, SourceReference
from sources.source_adapter import DocumentFetcher, SourceAdapter
from sources.source_registry import SourceDefinition
from support.semantic_fakes import HashingEmbedder

SIDOROV = (
    "Сергей Сидоров, известный правозащитник, задержан на антивоенном митинге. "
    "Активисты считают дело политически мотивированным."
)
PETROV = "Полиция задержала Петра Петрова за мелкое хулиганство. Составлен протокол по КоАП."
IVANOV = (
    "Иван Иванов, активист, задержан на антивоенном митинге. "
    "Правозащитники считают дело политически мотивированным."
)
MALFORMED_MARKER = "MALFORMED"


@dataclass
class FakeUpstream:
    """Listing order = insertion order; `fetches` counts document downloads."""

    articles: dict[str, tuple[str, str]] = field(default_factory=dict)
    fetches: list[str] = field(default_factory=list)
    discoveries: int = 0
    discovery_error: Exception | None = None

    def publish(self, external_id: str, text: str, *, title: str | None = None) -> None:
        self.articles[external_id] = (title or external_id, text)


class FakeSourceAdapter:
    def __init__(self, name: str, upstream: FakeUpstream) -> None:
        self._name = name
        self._upstream = upstream

    async def discover(self, *, limit: int) -> list[SourceReference]:
        self._upstream.discoveries += 1
        if self._upstream.discovery_error is not None:
            raise self._upstream.discovery_error
        return [
            SourceReference(external_id=external_id, url=f"https://{self._name}.test/{external_id}")
            for external_id in list(self._upstream.articles)[:limit]
        ]

    async def fetch(self, reference: SourceReference) -> RawDocument:
        self._upstream.fetches.append(reference.external_id)
        title, text = self._upstream.articles[reference.external_id]
        return RawDocument(
            external_id=reference.external_id,
            url=reference.url,
            fetched_at=datetime.now(UTC),
            content_type="text/plain",
            content=f"{title}\n{text}".encode(),
        )


class FakeArticleParser:
    def parse(self, raw_document: RawDocument) -> ParsedArticle:
        title, _, text = raw_document.content.decode().partition("\n")
        if not text:
            raise ParseError(f"No article text in {raw_document.external_id}")
        return ParsedArticle(
            external_id=raw_document.external_id,
            url=raw_document.url,
            title=title,
            published_at=None,
            text=text,
        )


def fake_source(name: str, upstream: FakeUpstream) -> SourceDefinition:
    def create_adapter(client: httpx.AsyncClient, fetcher: DocumentFetcher) -> SourceAdapter:
        return FakeSourceAdapter(name, upstream)

    return SourceDefinition(
        name=name,
        source_name=f"Fake {name}",
        base_url=f"https://{name}.test",
        create_adapter=create_adapter,
        create_parser=FakeArticleParser,
    )


class UnusedFetcher:
    async def fetch(self, reference: SourceReference) -> RawDocument:
        raise AssertionError("fake sources fetch through their adapter")


class MalformedAwareExtractor(RuleBasedEntityExtractor):
    """Rule-based extraction that rejects a marked article the way invalid input is rejected."""

    def extract(self, document: ExtractionDocument) -> list[RawMention]:
        if MALFORMED_MARKER in document.text:
            raise ValueError(f"article {document.article_id} has malformed markup")
        return super().extract(document)


def build_service(
    session_factory: sessionmaker[Session],
    upstreams: dict[str, FakeUpstream],
    *,
    create_semantic_indexer: Callable[[], SemanticIndexer] | None = None,
    discovery_limit: int = 10,
    # Tests run stages seconds apart; the production settle interval is covered separately.
    evidence_settle_interval: timedelta = timedelta(0),
    stale_run_after: timedelta | None = None,
) -> MonitoringService:
    return build_monitoring_service(
        session_factory,
        settings=MonitoringSettings(
            enabled_sources=tuple(upstreams),
            discovery_limit=discovery_limit,
            stale_run_after=stale_run_after or MonitoringSettings().stale_run_after,
        ),
        env={},
        sources={name: fake_source(name, upstream) for name, upstream in upstreams.items()},
        create_fetcher=UnusedFetcher,
        create_semantic_indexer=create_semantic_indexer,
        use_env_semantic_indexer=False,
        evidence_settle_interval=evidence_settle_interval,
        extraction_pipeline=ExtractionPipeline(
            extractors=[MalformedAwareExtractor()],
            normalizers=[RuleBasedMentionNormalizer()],
            event_extractor=RuleBasedEventExtractor(),
            persistence=SqlAlchemyExtractionPersistence(session_factory),
        ),
    )


def semantic_indexer(
    session_factory: sessionmaker[Session], store: VectorStore | None = None
) -> SemanticIndexer:
    """Real builders and PostgreSQL documents; in-process Qdrant (or the given store)."""
    return SemanticIndexer(
        builders={
            RetrievalEntityType.PERSON: PersonSemanticDocumentBuilder(session_factory),
            RetrievalEntityType.EVENT: EventSemanticDocumentBuilder(session_factory),
        },
        repository=SqlAlchemySemanticDocumentRepository(session_factory),
        embedder=HashingEmbedder(),
        store=store or QdrantVectorStore(QdrantClient(":memory:")),
        collections={
            RetrievalEntityType.PERSON: "persons_monitoring_test",
            RetrievalEntityType.EVENT: "events_monitoring_test",
        },
    )


def import_rf_snapshot(
    session_factory: sessionmaker[Session], rows: Sequence[tuple[str, str]]
) -> int:
    """`rows`: (full_name, birth date dd.mm.yyyy)."""
    lines = ["full_name,birth_date,inclusion_reason"]
    lines += [f"{name},{birth_date},test" for name, birth_date in rows]
    return (
        RosfinmonitoringIngestionPipeline(persistence=RosfinmonitoringPersistence(session_factory))
        .ingest(
            raw_content=("\n".join(lines) + "\n").encode(),
            source_url="https://rosfinmonitoring.test/list",
            snapshot_date=datetime.now(UTC),
        )
        .snapshot_id
    )


def table_counts(session_factory: sessionmaker[Session], *tables: str) -> dict[str, int]:
    with session_factory() as session:
        return {
            name: int(
                session.scalar(select(func.count()).select_from(Base.metadata.tables[name])) or 0
            )
            for name in tables
        }
