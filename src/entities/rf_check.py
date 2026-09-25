"""The person entities checked against the Rosfinmonitoring list.

The list is downloaded afresh from fedsfm.ru, stored as a new snapshot when it changed,
and every entity is compared with the latest imported snapshot by name. The news give
no birth date, so the patronymic is the only thing that tells a person from a namesake:

- full: given name, patronymic and surname are the list's — hidden from «Сущности»;
- name: given name and surname are, one side has no patronymic — shown, marked.

A different patronymic on both sides is another person. The entities stay: only the
matches are rewritten, so a wrong one is visible and the next check corrects it.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import httpx
from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import (
    EntityGroupRecord,
    EntityGroupRfMatchRecord,
    RosfinmonitoringEntryRecord,
)
from entities.disputes import BY_REGION, BY_RF, merge_clear_pairs
from rosfinmonitoring.ingestion import RosfinmonitoringIngestionPipeline
from rosfinmonitoring.parser import HtmlRosfinmonitoringParser
from rosfinmonitoring.persistence import RosfinmonitoringPersistence, compute_content_hash
from rosfinmonitoring.snapshot_lookup import SqlAlchemyRosfinmonitoringSnapshotLookup

logger = logging.getLogger("entities")

RF_LIST_URL = "https://www.fedsfm.ru/documents/terrorists-catalog-portal-act"
DOWNLOAD_TIMEOUT_SECONDS = 180.0
INSERT_CHUNK = 5_000
FULL = "full"
NAME = "name"


def download_rf_list() -> bytes:
    """The whole list page. fedsfm.ru's certificate is issued by the Russian state CA,
    absent from the usual trust store: certificate checking is off for this one request
    (the user's choice), so a tampered list on the way would go unnoticed."""
    response = httpx.get(
        RF_LIST_URL,
        timeout=DOWNLOAD_TIMEOUT_SECONDS,
        follow_redirects=True,
        verify=False,
        headers={"User-Agent": "Mozilla/5.0 (court-monitor)"},
    )
    response.raise_for_status()
    return response.content


@dataclass(frozen=True)
class RfCheckResult:
    snapshot_id: int | None
    snapshot_date: datetime | None
    entries: int
    # The download failed and the check used the last snapshot; why.
    download_error: str | None
    new_snapshot: bool
    entities: int
    full: int
    possible: int
    # Pairs of «Спорные случаи» taken for one person: one side is on the list, or a
    # name without a patronymic had one full name to be.
    merged: int = 0
    merged_region: int = 0


def _fold(text: str) -> list[str]:
    return text.lower().replace("ё", "е").split()


def _entity_parts(name: str) -> tuple[str, str, str | None] | None:
    """(given name, surname, patronymic) of «Имя [Отчество] Фамилия»; None for a single
    word. An initial («Соломатин П.») never equals a name of the list, so never matches."""
    words = _fold(name)
    if len(words) < 2:
        return None
    return words[0], words[-1], " ".join(words[1:-1]) or None


def _entry_parts(normalized_name: str) -> tuple[str, str, str | None] | None:
    """The list writes «ФАМИЛИЯ ИМЯ ОТЧЕСТВО»."""
    words = _fold(normalized_name)
    if len(words) < 2:
        return None
    return words[1], words[0], " ".join(words[2:]) or None


def match_level(entity_patronymic: str | None, entry_patronymic: str | None) -> str | None:
    if entity_patronymic and entry_patronymic:
        return FULL if entity_patronymic == entry_patronymic else None
    return NAME


class EntityRfCheck:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        download: Callable[[], bytes] = download_rf_list,
        on_stage: Callable[[str], None] = lambda _stage: None,
    ) -> None:
        self._session_factory = session_factory
        self._download = download
        self._on_stage = on_stage

    def run(self) -> RfCheckResult:
        download_error, new_snapshot = self._refresh_list()
        latest = SqlAlchemyRosfinmonitoringSnapshotLookup(
            self._session_factory
        ).latest_imported_snapshot()
        if latest is None:
            logger.warning("event=entities_rf_check_no_snapshot error=%s", download_error)
            return RfCheckResult(None, None, 0, download_error, new_snapshot, 0, 0, 0)

        self._on_stage("matching")
        with self._session_factory() as session:
            by_name: dict[tuple[str, str], list[tuple[int, str | None]]] = defaultdict(list)
            for entry_id, normalized_name in session.execute(
                select(
                    RosfinmonitoringEntryRecord.id, RosfinmonitoringEntryRecord.normalized_name
                ).where(RosfinmonitoringEntryRecord.snapshot_id == latest.snapshot_id)
            ).all():
                if (parts := _entry_parts(normalized_name)) is not None:
                    given, surname, patronymic = parts
                    by_name[given, surname].append((entry_id, patronymic))
            groups = session.execute(select(EntityGroupRecord.id, EntityGroupRecord.name)).all()
        rows: list[dict[str, object]] = []
        levels: dict[int, str] = {}
        for group_id, name in groups:
            if (parts := _entity_parts(name)) is None:
                continue
            given, surname, patronymic = parts
            for entry_id, entry_patronymic in by_name.get((given, surname), []):
                level = match_level(patronymic, entry_patronymic)
                if level is None:
                    continue
                rows.append({"group_id": group_id, "entry_id": entry_id, "level": level})
                if levels.get(group_id) != FULL:
                    levels[group_id] = level

        self._on_stage("writing")
        with self._session_factory.begin() as session:
            session.execute(delete(EntityGroupRfMatchRecord))
            for start in range(0, len(rows), INSERT_CHUNK):
                session.execute(
                    insert(EntityGroupRfMatchRecord), rows[start : start + INSERT_CHUNK]
                )
            self._on_stage("merging")
            merged = merge_clear_pairs(session, listed_level=FULL)
        full = sum(level == FULL for level in levels.values())
        result = RfCheckResult(
            snapshot_id=latest.snapshot_id,
            snapshot_date=latest.snapshot_date,
            entries=latest.entry_count,
            download_error=download_error,
            new_snapshot=new_snapshot,
            entities=len(groups),
            full=full,
            possible=len(levels) - full,
            merged=merged[BY_RF],
            merged_region=merged[BY_REGION],
        )
        logger.info(
            "event=entities_rf_checked snapshot_id=%s new_snapshot=%s entities=%d full=%d "
            "possible=%d merged=%d merged_region=%d download_error=%s",
            result.snapshot_id,
            result.new_snapshot,
            result.entities,
            result.full,
            result.possible,
            result.merged,
            result.merged_region,
            result.download_error,
        )
        return result

    def _refresh_list(self) -> tuple[str | None, bool]:
        """(why the fresh list could not be had, whether it became a new snapshot).

        Any failure leaves the last snapshot in use: the check still runs."""
        self._on_stage("downloading")
        try:
            content = self._download()
        except httpx.HTTPError as exc:
            logger.warning("event=rf_list_download_failed error=%s", exc)
            return f"{type(exc).__name__}: {exc}", False
        persistence = RosfinmonitoringPersistence(self._session_factory)
        if persistence.snapshot_exists(compute_content_hash(content)):
            logger.info("event=rf_list_unchanged bytes=%d", len(content))
            return None, False
        self._on_stage("importing")
        try:
            imported = RosfinmonitoringIngestionPipeline(
                persistence, HtmlRosfinmonitoringParser()
            ).ingest(content, source_url=RF_LIST_URL)
        except ValueError as exc:  # the page parsed to no entries: the site changed
            logger.warning("event=rf_list_unusable error=%s", exc)
            return f"список не разобран: {exc}", False
        logger.info(
            "event=rf_list_imported snapshot_id=%s entries=%d",
            imported.snapshot_id,
            imported.entries_created,
        )
        return None, True
