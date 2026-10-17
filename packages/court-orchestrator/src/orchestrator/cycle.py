"""The operator cycle («Рабочий цикл», ADR 0021): which module does which step, in what
order. Coordination only: each step is one call of a module's public API, and what a
step found is the module's own result.

    load       a monitoring run of every source, without resolution (`orchestrator.service`)
    purge      processing: articles before the working date and junk go
    entities   processing.identity: the entity groups are rebuilt
    figurants  processing: who a case is opened against
    political  analysis, with two readings of processing in between: the list, the
               verdicts, the digests, the news, the unnamed, the sentences

The steps are started one process each by `orchestrator.operations`; this module is
what such a process runs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from analysis.service import (
    AnalysisService,
    NewsResult,
    PoliticsResult,
    RfCheckResult,
    UnnamedResult,
)
from processing.identity.service import CollectResult, IdentityService
from processing.service import (
    DigestResult,
    FigurantResult,
    JunkPurgeOutcome,
    ProcessingService,
    SentencesResult,
)

logger = logging.getLogger("monitoring")


@dataclass(frozen=True)
class PoliticalStep:
    """What the last step of the cycle found, part by part."""

    # None with `rf_error`: the list could not be checked and the verdicts ran anyway.
    rf: RfCheckResult | None
    rf_error: str | None
    politics: PoliticsResult
    digests: DigestResult
    news: NewsResult
    unnamed: UnnamedResult
    sentences: SentencesResult


class OperatorCycle:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        processing: ProcessingService,
        identity: IdentityService,
        analysis: AnalysisService,
    ) -> None:
        self._session_factory = session_factory
        self._processing = processing
        self._identity = identity
        self._analysis = analysis

    def purge(self) -> JunkPurgeOutcome:
        return self._processing.purge_junk()

    def entities(self) -> CollectResult:
        if not self._identity.names_by_model:
            logger.warning(
                "event=entity_names_no_model: neither OPENROUTER_API_KEY nor ANTHROPIC_API_KEY"
            )
        return self._identity.rebuild_groups(
            self._session_factory,
            on_stage=lambda stage: logger.info("event=entities_collect_stage stage=%s", stage),
        )

    def figurants(self) -> FigurantResult:
        return self._processing.find_figurants(
            on_stage=lambda stage: logger.info("event=entity_figurants_stage stage=%s", stage)
        )

    def check_rosfinmonitoring(self) -> RfCheckResult:
        return self._analysis.check_rosfinmonitoring(
            on_stage=lambda stage: logger.info("event=entities_rf_check_stage stage=%s", stage)
        )

    def news(self) -> NewsResult:
        return self._analysis.find_news(
            on_stage=lambda stage: logger.info("event=entity_news_stage stage=%s", stage)
        )

    def unnamed(self) -> UnnamedResult:
        return self._analysis.find_unnamed(
            on_stage=lambda stage: logger.info("event=unnamed_stage stage=%s", stage)
        )

    def sentences(self) -> SentencesResult:
        return self._processing.read_sentences(
            on_stage=lambda stage: logger.info("event=sentences_stage stage=%s", stage)
        )

    def political(self) -> PoliticalStep:
        # The list first: it confirms who is who before the verdicts. Its failure (the
        # site, the database) must not cost the verdicts: they run, and the output says why
        # the list was not checked.
        rf_error: str | None = None
        rf: RfCheckResult | None = None
        try:
            rf = self.check_rosfinmonitoring()
        except Exception as exc:  # reported in the output; the step goes on
            logger.exception("event=entities_rf_check_failed")
            rf_error = f"{type(exc).__name__}: {exc}"
        politics = self._analysis.classify_politics(
            on_stage=lambda stage: logger.info("event=entity_politics_stage stage=%s", stage)
        )
        # Roundup posts («Главное за день») among the figurants' sources: classified once
        # per article, so «Публикации» on /ui/political can skip them.
        digests = self._processing.read_digests(
            on_stage=lambda stage: logger.info("event=article_digest_stage stage=%s", stage)
        )
        # What each political case's latest news is: the new cases and the sentences first.
        news = self.news()
        # The final step ends with unnamed figurants: their cases are read the same way.
        unnamed = self.unnamed()
        # Last: what the sentences are, for the counts of «Спросить». Nothing above reads it.
        sentences = self.sentences()
        return PoliticalStep(
            rf=rf,
            rf_error=rf_error,
            politics=politics,
            digests=digests,
            news=news,
            unnamed=unnamed,
            sentences=sentences,
        )
