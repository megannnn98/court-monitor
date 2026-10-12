"""The analysis module's public API: the verdicts.

The canonical path works on entity groups (ADR 0024): is a group on the
Rosfinmonitoring list, is its case political, what is the latest of it, and who the
figurants without a name may be. Each of those reads everything pending in one call and
keeps its answers; a model, where one is asked, stays behind this module.

The person path (`*_person*`) is what the monitoring run still calls, one person per
call: the rule classifier and the list matcher over canonical persons. It is kept for
that run and is not extended.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Protocol

from sqlalchemy.orm import Session, sessionmaker

from analysis.news.case_news import NewsFinder, NewsResult, news_reader_from_env
from analysis.persecution.classification_service import PersecutionClassificationService
from analysis.politics.classification import (
    PoliticsFinder,
    PoliticsResult,
    politics_classifier_from_env,
)
from analysis.rosfinmonitoring.matcher import RuleBasedRosfinmonitoringMatcher
from analysis.rosfinmonitoring.matcher_persistence import RosfinMatchPersistence
from analysis.rosfinmonitoring.rf_check import EntityRfCheck, RfCheckResult
from analysis.rosfinmonitoring.snapshot_lookup import (
    RosfinmonitoringSnapshotSummary,
    SqlAlchemyRosfinmonitoringSnapshotLookup,
)
from analysis.unnamed.figurants import UnnamedFinder, UnnamedResult
from processing.evidence.unnamed import unnamed_reader_from_env

# The steps log under the name the operator console reads its runs by.
logger = logging.getLogger("monitoring")

__all__ = [
    "AnalysisService",
    "NewsResult",
    "PoliticsResult",
    "RfCheckResult",
    "RosfinmonitoringSnapshotSummary",
    "UnnamedResult",
]

OnStage = Callable[[str], None]


class PendingPersonAnalysis(Protocol):
    def persons_pending_classification(
        self, *, classifier_name: str, classifier_version: str
    ) -> list[int]: ...

    def persons_pending_rf_match(
        self, *, snapshot_id: int, matcher_name: str, matcher_version: str
    ) -> list[int]: ...


class AnalysisService:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        classification: PersecutionClassificationService,
        rf_matcher: RuleBasedRosfinmonitoringMatcher,
        rf_persistence: RosfinMatchPersistence,
        snapshot_lookup: SqlAlchemyRosfinmonitoringSnapshotLookup,
        pending: PendingPersonAnalysis,
    ) -> None:
        self._session_factory = session_factory
        self._classification = classification
        self._rf_matcher = rf_matcher
        self._rf_persistence = rf_persistence
        self._snapshot_lookup = snapshot_lookup
        self._pending = pending

    # -- entity groups: the canonical path -------------------------------------------------

    def check_rosfinmonitoring(self, *, on_stage: OnStage) -> RfCheckResult:
        """Download the list (a new snapshot when it changed) and match every entity
        group against the latest one by name."""
        return EntityRfCheck(self._session_factory, on_stage=on_stage).run()

    def classify_politics(self, *, on_stage: OnStage) -> PoliticsResult:
        """Political persecution or common crime, for every figurant: rules, then a model."""
        classifier = politics_classifier_from_env()
        if classifier is None:
            logger.warning("event=entity_politics_no_model: OPENROUTER_API_KEY is not set")
        return PoliticsFinder(self._session_factory, classifier=classifier, on_stage=on_stage).run()

    def find_news(self, *, on_stage: OnStage) -> NewsResult:
        """What each political case's latest news is: a new case, a sentence, or more."""
        reader = news_reader_from_env()
        if reader is None:
            logger.warning("event=entity_news_no_model: OPENROUTER_API_KEY is not set")
        return NewsFinder(self._session_factory, reader=reader, on_stage=on_stage).run()

    def find_unnamed(self, *, on_stage: OnStage) -> UnnamedResult:
        """The figurants the publications do not name, for matching with the list."""
        reader = unnamed_reader_from_env()
        if reader is None:
            logger.warning("event=unnamed_no_model: OPENROUTER_API_KEY is not set")
        return UnnamedFinder(self._session_factory, reader=reader, on_stage=on_stage).run()

    # -- persons: the compatibility path of the monitoring run -------------------------------

    @property
    def person_classifier(self) -> str:
        classifier = self._classification.classifier
        return f"{classifier.classifier_name}@{classifier.classifier_version}"

    def persons_pending_classification(self) -> list[int]:
        classifier = self._classification.classifier
        return self._pending.persons_pending_classification(
            classifier_name=classifier.classifier_name,
            classifier_version=classifier.classifier_version,
        )

    def classify_person(self, person_id: int) -> str:
        """Classify one person and store it; the status."""
        return str(self._classification.classify_person(person_id).status)

    def latest_snapshot(self) -> RosfinmonitoringSnapshotSummary | None:
        """The published list the database works against now; None before the first.
        Never the operator's own Airtable list."""
        return self._snapshot_lookup.latest_imported_snapshot()

    def latest_snapshot_id(self) -> int | None:
        snapshot = self.latest_snapshot()
        return None if snapshot is None else snapshot.snapshot_id

    def persons_pending_rf_match(self, snapshot_id: int) -> list[int]:
        return self._pending.persons_pending_rf_match(
            snapshot_id=snapshot_id,
            matcher_name=self._rf_matcher.matcher_name,
            matcher_version=self._rf_matcher.matcher_version,
        )

    def match_person(self, person_id: int, snapshot_id: int) -> str:
        """Match one person against the snapshot and store it; the status."""
        result = self._rf_matcher.match_person(person_id, snapshot_id)
        self._rf_persistence.save_match_result(result)
        return result.status.value
