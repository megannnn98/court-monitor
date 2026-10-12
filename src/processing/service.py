"""The processing module's public API: from stored articles to people and what the
publications say of them.

Extraction is one article per call, so the caller keeps its own loop. The steps of the
operator cycle (purge, figurants, digest, sentences) work through everything pending in
one call. Identity has its own door, `processing.identity.service`. Every outcome is
persisted; the next stage selects its work from the database.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.orm import Session, sessionmaker

from processing.evidence.digest import (
    DigestFinder,
    DigestResult,
    digest_classifier_from_env,
    figurant_titles,
)
from processing.evidence.publication_vectors import (
    PassageEmbedder,
    PublicationVectorIndexer,
    VectorIndexResult,
)
from processing.evidence.sentences import SentenceFinder, SentencesResult, sentence_reader_from_env
from processing.extraction.documents import SqlAlchemyExtractionDocumentRepository
from processing.extraction.models import ExtractionRunStatus, ExtractionSaveResult
from processing.extraction.pipeline import ExtractionPipeline, ExtractionVersions
from processing.junk.hold_reader import HoldsRead, hold_reader_from_env, read_holds
from processing.junk.purge import JunkPurge, JunkPurgeResult, since_from_env
from processing.junk.screen import screen_from_env
from processing.roles.roles import FigurantFinder, FigurantResult, role_classifier_from_env

# The steps log under the name the operator console reads its runs by.
logger = logging.getLogger("monitoring")

__all__ = [
    "DigestResult",
    "ExtractionRunStatus",
    "ExtractionSaveResult",
    "FigurantResult",
    "JunkPurgeOutcome",
    "ProcessingService",
    "SentencesResult",
]

OnStage = Callable[[str], None]


class PendingExtraction(Protocol):
    """Which stored articles of a source have no extraction run for these versions."""

    def articles_pending_extraction(
        self, *, source_base_url: str, versions: ExtractionVersions
    ) -> list[int]: ...


@dataclass(frozen=True)
class JunkPurgeOutcome:
    purge: JunkPurgeResult
    # What a model made of the articles the screen held back.
    holds: HoldsRead
    # What the screen's own model cost: of the embedding screen, nothing.
    screen_cost_usd: float


class ProcessingService:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        pipeline: ExtractionPipeline,
        documents: SqlAlchemyExtractionDocumentRepository,
        pending: PendingExtraction,
    ) -> None:
        self._session_factory = session_factory
        self._pipeline = pipeline
        self._documents = documents
        self._pending = pending

    @property
    def extraction_versions(self) -> ExtractionVersions:
        return self._pipeline.versions

    def articles_pending_extraction(self, *, source_base_url: str) -> list[int]:
        return self._pending.articles_pending_extraction(
            source_base_url=source_base_url, versions=self._pipeline.versions
        )

    def extract_article(self, article_id: int) -> ExtractionSaveResult:
        """A FAILED status is an outcome of the article, stored with its run; an
        exception is a failure of the call."""
        return self._pipeline.run(self._documents.get_by_article_id(article_id))

    def purge_junk(self) -> JunkPurgeOutcome:
        """Delete the articles before the working date and those without a criminal
        case; what the screen holds back, a model reads whole. Irreversible."""

        def progress(result: JunkPurgeResult) -> None:
            logger.info(
                "event=junk_purge_progress articles=%d total=%d persons=%d reviews=%d outdated=%d "
                "held=%d",
                result.articles,
                total,
                result.persons,
                result.reviews,
                result.outdated,
                result.held,
            )

        since = since_from_env()
        # On, the screen is loaded and tried here: broken, it stops the purge before any delete.
        screen = screen_from_env()
        if screen is not None:
            logger.info("event=junk_screen_on screen=%s cutoff=%.4f", screen.name, screen.cutoff)
        purge = JunkPurge(self._session_factory, on_progress=progress, since=since, screen=screen)
        total = purge.count()
        logger.info(
            "event=junk_purge_started total=%d since=%s", total, since.date() if since else "-"
        )
        result = purge.run()
        # What the screen held, a model reads whole: a case goes on into the work, the rest
        # waits for a person with the model's reason.
        holds = read_holds(self._session_factory, hold_reader_from_env())
        return JunkPurgeOutcome(
            purge=result,
            holds=holds,
            screen_cost_usd=round(getattr(screen, "cost_usd", 0.0), 6),
        )

    def find_figurants(self, *, on_stage: OnStage) -> FigurantResult:
        """Tell the people a criminal case is opened against from those only mentioned."""
        classifier = role_classifier_from_env()
        if classifier is None:
            logger.warning("event=entity_roles_no_model: OPENROUTER_API_KEY is not set")
        return FigurantFinder(self._session_factory, classifier=classifier, on_stage=on_stage).run()

    def read_digests(self, *, on_stage: OnStage) -> DigestResult:
        """Roundup posts («Главное за день») among the figurants' sources, classified
        once per article so a reader can skip them."""
        classifier = digest_classifier_from_env()
        if classifier is None:
            logger.warning("event=article_digest_no_model: OPENROUTER_API_KEY is not set")
        with self._session_factory() as session:
            titles = figurant_titles(session)
        return DigestFinder(self._session_factory, classifier=classifier, on_stage=on_stage).run(
            titles
        )

    def read_sentences(self, *, on_stage: OnStage) -> SentencesResult:
        """The sentences the articles tell of: region, punishment and reason of each."""
        reader = sentence_reader_from_env()
        if reader is None:
            logger.warning("event=sentences_no_model: OPENROUTER_API_KEY is not set")
        return SentenceFinder(self._session_factory, reader=reader, on_stage=on_stage).run()

    def index_publication_vectors(self, embedder: PassageEmbedder) -> VectorIndexResult:
        """The windows of every stored publication as vectors of `embedder`'s model; an
        article whose text has not changed is skipped. On demand, not in the cycle."""
        return PublicationVectorIndexer(self._session_factory, embedder).run()
