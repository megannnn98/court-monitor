"""The person entities checked against the Rosfinmonitoring list.

The list is downloaded afresh from fedsfm.ru, stored as a new snapshot when it changed,
and every entity is compared with the latest imported snapshot by name. The news give
no birth date, so the patronymic is the only thing that tells a person from a namesake:

- full: given name, patronymic and surname are the list's — who the person is,
  confirmed by the entry's birth date and place (the list drops nobody);
- name: given name and surname are, one side has no patronymic — maybe a namesake.

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
from rosfinmonitoring.download import (
    RF_LIST_URL,
    RosfinmonitoringDownloadError,
    download_rf_list,
)
from rosfinmonitoring.inclusion_dates import (
    InclusionDates,
    InclusionDatesUnavailable,
    download_inclusion_dates,
)
from rosfinmonitoring.inclusion_store import (
    backfill_birth_dates,
    carry_dates_from_previous_snapshot,
    write_inclusion_dates,
)
from rosfinmonitoring.ingestion import RosfinmonitoringIngestionPipeline
from rosfinmonitoring.parser import HtmlRosfinmonitoringParser
from rosfinmonitoring.persistence import RosfinmonitoringPersistence, compute_content_hash
from rosfinmonitoring.snapshot_lookup import SqlAlchemyRosfinmonitoringSnapshotLookup

logger = logging.getLogger("entities")

INSERT_CHUNK = 5_000
# The ОВД-Инфо file is 1.5 MB and the page that names it is small; a slow answer is
# better than a cut-off, and the check carries on either way.
INCLUSION_TIMEOUT_SECONDS = 120.0
FULL = "full"
NAME = "name"


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
    """Words as compared, both sides alike: «ё» is «е», and «-ье-»/«-ья» are «-ие-»/«-ия»
    — the list and the news spell «Валериевна» and «Валерьевна», «Наталия» and «Наталья»
    for one person."""
    folded = text.lower().replace("ё", "е")
    for soft, plain in (("ье", "ие"), ("ья", "ия"), ("ьи", "ии")):
        folded = folded.replace(soft, plain)
    return folded.split()


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
        inclusion_dates: Callable[[httpx.Client], InclusionDates] = download_inclusion_dates,
        on_stage: Callable[[str], None] = lambda _stage: None,
    ) -> None:
        self._session_factory = session_factory
        self._download = download
        self._inclusion_dates = inclusion_dates
        self._on_stage = on_stage

    def run(self) -> RfCheckResult:
        download_error, new_snapshot = self._refresh_list()
        lookup = SqlAlchemyRosfinmonitoringSnapshotLookup(self._session_factory)
        latest = lookup.latest_imported_snapshot()
        if latest is None:
            logger.warning("event=entities_rf_check_no_snapshot error=%s", download_error)
            return RfCheckResult(None, None, 0, download_error, new_snapshot, 0, 0, 0)
        # Every run, not only the one that imported: a snapshot imported on a day the
        # ОВД-Инфо file was unreadable arrives with no days at all, and the page that
        # filters by them would stay empty until the state next changed the list — which
        # may be weeks. The snapshot in the database on the day the code is deployed is
        # exactly such a snapshot, so the days are topped up at every check.
        self._fill_inclusion_dates(latest.snapshot_id)
        # The operator's Airtable list, consulted for probabilities only: a name it has
        # and the published list does not is «possible», never FULL, so it can neither
        # count as confirmed nor merge a pair of namesakes.
        airtable = lookup.airtable_snapshot()

        self._on_stage("matching")
        with self._session_factory() as session:
            by_name = self._by_name(session, latest.snapshot_id)
            probable = self._by_name(session, airtable.snapshot_id) if airtable is not None else {}
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
            # Only a name the published list does not have is a probable match; where
            # both lists have it, the official FULL above already stands.
            if levels.get(group_id) is not None and levels[group_id] != FULL:
                continue
            for entry_id, entry_patronymic in probable.get((given, surname), []):
                if match_level(patronymic, entry_patronymic) is None:
                    continue
                if group_id not in levels:
                    rows.append({"group_id": group_id, "entry_id": entry_id, "level": NAME})
                    levels[group_id] = NAME

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

    @staticmethod
    def _by_name(
        session: Session, snapshot_id: int
    ) -> dict[tuple[str, str], list[tuple[int, str | None]]]:
        """(given, surname) → [(entry id, patronymic)] of one snapshot's entries.

        Two snapshots are read the same way; they are kept apart because a hit in one is
        a confirmed match and a hit in the other only a probable one.
        """
        by_name: dict[tuple[str, str], list[tuple[int, str | None]]] = defaultdict(list)
        for entry_id, normalized_name in session.execute(
            select(
                RosfinmonitoringEntryRecord.id, RosfinmonitoringEntryRecord.normalized_name
            ).where(RosfinmonitoringEntryRecord.snapshot_id == snapshot_id)
        ).all():
            if (parts := _entry_parts(normalized_name)) is not None:
                given, surname, patronymic = parts
                by_name[given, surname].append((entry_id, patronymic))
        return by_name

    def _refresh_list(self) -> tuple[str | None, bool]:
        """(why the fresh list could not be had, whether it became a new snapshot).

        Any failure leaves the last snapshot in use: the check still runs."""
        self._on_stage("downloading")
        try:
            content = self._download()
        except (httpx.HTTPError, RosfinmonitoringDownloadError) as exc:
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

    def _fill_inclusion_dates(self, snapshot_id: int) -> None:
        """Give the snapshot's entries whatever day of inclusion can be established.

        Three steps, in this order, and the order matters:

        - first the birth dates, re-read from the line each entry keeps in `raw_data`. A
          snapshot imported before the parser understood the former names in brackets has
          795 entries with no date of birth, and those can never be told from a namesake;
        - then the previous snapshot, which is our own record of the same list a week ago.
          A name and a birth date that match there already have their day, and the day does
          not change because the list was re-downloaded. This is what fills a snapshot
          imported on a day the file could not be read, and it is why an unreachable file
          does not leave the page empty. It has to come after the birth dates: an entry
          whose date of birth was just recovered matches on a pair, and the carrying query
          skips entries that have no birth date — so carried before it, those 825 entries
          would miss the day they could have had;
        - then the ОВД-Инфо copy, for whatever is left: a person added since, or a
          snapshot whose days were never established at all.

        A failure to read the file is therefore not a failure of this at all: what the
        previous snapshot knew is already written, and the run goes on.
        """
        with self._session_factory.begin() as session:
            birth_dates = backfill_birth_dates(session, snapshot_id)
        carried = carry_dates_from_previous_snapshot(self._session_factory, snapshot_id)
        try:
            with httpx.Client(timeout=INCLUSION_TIMEOUT_SECONDS) as client:
                dates = self._inclusion_dates(client)
        except (httpx.HTTPError, InclusionDatesUnavailable) as exc:
            logger.warning(
                "event=rfm_inclusion_dates_unavailable carried=%d error=%s", carried, exc
            )
            return
        with self._session_factory.begin() as session:
            result = write_inclusion_dates(session, snapshot_id, dates)
        logger.info(
            "event=rfm_inclusion_dates_ok snapshot_id=%s carried=%d birth_dates=%d dated=%d "
            "already=%d unmatched=%d",
            snapshot_id,
            carried,
            birth_dates,
            result.dated,
            result.already_dated,
            sum(result.unmatched.values()),
        )
