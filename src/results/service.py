"""The results module's public API: what a reader is shown.

Findings are recorded from the verdicts of analysis and listed here; a person's dossier
is read from what the pipeline stored. Nothing in this module decides a verdict.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.orm import Session, sessionmaker

from results import dossier
from results.dossier import EntityHit, PublicationQuote, StatedFact
from results.findings import NO_RF_SNAPSHOT, FindingEvaluation, MonitoringFindingService
from results.models import MonitoringFindingView

__all__ = [
    "NO_RF_SNAPSHOT",
    "EntityHit",
    "FindingEvaluation",
    "PublicationQuote",
    "ResultsService",
    "StatedFact",
]


class ResultsService:
    def __init__(
        self, *, session_factory: sessionmaker[Session], findings: MonitoringFindingService
    ) -> None:
        self._session_factory = session_factory
        self._findings = findings

    # -- findings of the monitoring run ------------------------------------------------------

    def evaluate_findings(self, *, run_id: int, snapshot_id: int | None) -> FindingEvaluation:
        """Record which persons satisfy each finding criterion now."""
        return self._findings.evaluate(run_id=run_id, snapshot_id=snapshot_id)

    def list_findings(
        self, *, active_only: bool = True, limit: int = 100, offset: int = 0
    ) -> list[MonitoringFindingView]:
        return self._findings.list_findings(active_only=active_only, limit=limit, offset=offset)

    # -- a person's dossier: entity groups (ADR 0024) ----------------------------------------

    def find_entities(self, name: str, *, limit: int = 10) -> list[EntityHit]:
        """The people whose name holds every word asked for, the most mentioned first."""
        with self._session_factory() as session:
            return dossier.find_entities(session, name, limit=limit)

    def entity_facts(self, key: str) -> list[StatedFact] | None:
        """What the pipeline decided of the person; None when nobody has this key."""
        with self._session_factory() as session:
            return dossier.entity_facts(session, key)

    def entity_evidence(self, key: str, *, limit: int = 20) -> list[PublicationQuote]:
        """The places the publications name the person, the latest first."""
        with self._session_factory() as session:
            return dossier.entity_evidence(session, key, limit=limit)

    def entity_publication_quotes(
        self, key: str, terms: Sequence[str], *, limit: int = 10
    ) -> list[PublicationQuote]:
        """Where the person's own publications say one of `terms`."""
        with self._session_factory() as session:
            return dossier.entity_publication_quotes(session, key, terms, limit=limit)

    def stored_publication_quotes(self, name: str, *, limit: int = 10) -> list[PublicationQuote]:
        """The stored publications whose text holds every word of the name."""
        with self._session_factory() as session:
            return dossier.stored_publication_quotes(session, name, limit=limit)
