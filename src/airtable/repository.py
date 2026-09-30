"""Writing the four Airtable lists into PostgreSQL, one table at a time.

Each function is idempotent: it matches rows by the Airtable record id stored in
`external_id`, and reports what it did instead of rebuilding the table. A record that
disappeared from Airtable is never deleted here — a list a person curates is theirs to
remove, not the sync's.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from airtable.client import AirtableRecord
from airtable.models import TableSyncResult
from db.orm_models import (
    AirtableKnownPersonRecord,
    ExcludedPersonRecord,
    RosfinmonitoringEntryRecord,
    RosfinmonitoringSnapshotRecord,
    Source,
)
from extraction.normalizers import RuleBasedMentionNormalizer
from rosfinmonitoring.models import AIRTABLE_SNAPSHOT_SOURCE_URL
from rosfinmonitoring.parser import _create_matching_key, _normalize_name, _parse_date

logger = logging.getLogger("airtable")

# The snapshot a sync of `rfm_persons` writes into. It is an ordinary snapshot, but not
# the list: `snapshot_lookup.latest_imported_snapshot` skips it, and a name in it alone
# is a probable match (`rosfinmonitoring.probable`).
RFM_SOURCE_URL = AIRTABLE_SNAPSHOT_SOURCE_URL

# Airtable columns are named by whoever made the base, so each field is looked up under
# the names it plausibly carries. The first non-empty one wins.
# The column a person's name lives in, under the names hand-kept lists give it. A base
# that tracks cases per person calls it «Преследуемый».
_NAME_FIELDS = (
    "full_name",
    "Full name",
    "Name",
    "ФИО",
    "Имя",
    "Фамилия Имя",
    "Преследуемый",
)
_BIRTH_DATE_FIELDS = ("birth_date", "Birth date", "Дата рождения")
# The `active` flag, under the names a hand-kept list actually gives it. Airtable's own
# membership column is accepted too, so a list exported straight from a base that tracks
# Rosfinmonitoring membership needs no renaming.
_ACTIVE_FIELDS = (
    "active",
    "Active",
    "Активен",
    "Включён",
    "✦Росфинмониторинг",
    "Росфинмониторинг",
)
_BASE_URL_FIELDS = ("base_url", "Base URL", "url", "URL", "Ссылка", "Адрес")

_normalizer = RuleBasedMentionNormalizer()


def person_name(full_name: str) -> tuple[str, str]:
    """The name as `persons` stores it, and its matching key."""
    normalized = _normalizer.normalize_person(full_name)[1]
    return normalized.full_name, normalized.matching_key


def _same(row: Any, values: dict[str, Any]) -> bool:
    return all(getattr(row, field_name) == value for field_name, value in values.items())


def _write(row: Any, values: dict[str, Any]) -> None:
    for field_name, value in values.items():
        setattr(row, field_name, value)


def sync_sources(session: Session, records: Sequence[AirtableRecord]) -> TableSyncResult:
    """Refresh the name and the flag of the sources Airtable lists.

    A source row exists only because the code registry or a downloaded article made it,
    so a record naming a `base_url` the database has never seen is reported, not
    created: a row nothing can load would only be a lie in the list.
    """
    result = TableSyncResult(received=len(records))
    by_url: dict[str, list[Source]] = {}
    for row in session.scalars(select(Source)):
        by_url.setdefault(_base_url_key(row.base_url), []).append(row)

    claimed: set[int] = set()
    for record in records:
        raw_url = record.text(*_BASE_URL_FIELDS)
        candidates = by_url.get(_base_url_key(raw_url)) or []
        match: Source | None = next(
            (found for found in candidates if found.id not in claimed), None
        )
        if match is None:
            # Not a source this build can load: nothing to name or to switch off.
            logger.warning("event=airtable_source_not_in_registry base_url=%s", raw_url)
            continue
        claimed.add(match.id)
        name = record.text("name", "Name", "Название", "Имя") or match.name
        active = record.flag(*_ACTIVE_FIELDS, default=True)
        if match.external_id == record.id and match.name == name and match.active == active:
            result.unchanged += 1
            continue
        match.external_id, match.name, match.active = record.id, name, active
        result.updated += 1
    session.commit()
    return result


def sync_known_persons(session: Session, records: Sequence[AirtableRecord]) -> TableSyncResult:
    """Upsert the people a person checked by hand, by Airtable record id."""
    result = TableSyncResult(received=len(records))
    existing = {
        str(row.external_id): row
        for row in session.scalars(
            select(AirtableKnownPersonRecord).where(
                AirtableKnownPersonRecord.external_id.in_({record.id for record in records})
            )
        )
    }
    for record in records:
        full_name = record.text(*_NAME_FIELDS)
        if not full_name:
            result.errors += 1
            logger.warning("event=airtable_known_person_without_name record=%s", record.id)
            continue
        normalized, matching_key = person_name(full_name)
        values: dict[str, Any] = {
            "full_name": full_name,
            "normalized_name": normalized,
            "matching_key": matching_key,
            "active": record.flag(*_ACTIVE_FIELDS, default=True),
        }
        row = existing.get(record.id)
        if row is None:
            session.add(AirtableKnownPersonRecord(external_id=record.id, **values))
            result.created += 1
        elif _same(row, values):
            result.unchanged += 1
        else:
            _write(row, values)
            result.updated += 1
    session.commit()
    return result


def sync_excluded_persons(session: Session, records: Sequence[AirtableRecord]) -> TableSyncResult:
    """Upsert the people who must never become target figurants."""
    result = TableSyncResult(received=len(records))
    existing = {
        str(row.external_id): row
        for row in session.scalars(
            select(ExcludedPersonRecord).where(
                ExcludedPersonRecord.external_id.in_({record.id for record in records})
            )
        )
    }
    for record in records:
        full_name = record.text(*_NAME_FIELDS)
        if not full_name:
            result.errors += 1
            logger.warning("event=airtable_excluded_person_without_name record=%s", record.id)
            continue
        normalized, _ = person_name(full_name)
        values: dict[str, Any] = {
            "full_name": full_name,
            "normalized_name": normalized,
            "category": record.text("category", "Category", "Категория", "Должность") or "other",
            "reason": record.text("reason", "Reason", "Причина", "Комментарий") or None,
            "active": record.flag(*_ACTIVE_FIELDS, default=True),
        }
        row = existing.get(record.id)
        if row is None:
            session.add(ExcludedPersonRecord(external_id=record.id, **values))
            result.created += 1
        elif _same(row, values):
            result.unchanged += 1
        else:
            _write(row, values)
            result.updated += 1
    session.commit()
    return result


def sync_rfm_persons(session: Session, records: Sequence[AirtableRecord]) -> TableSyncResult:
    """Refresh the Airtable-sourced Rosfinmonitoring snapshot, entry by entry.

    The rows land in `rosfinmonitoring_entries` under a snapshot of our own, so the
    matcher treats them exactly as it treats a downloaded list. Entries are matched
    within that snapshot by name and birth date, the pair the table makes unique.

    Unlike the curated lists, entries whose Airtable record is gone are dropped from
    this snapshot: a snapshot is a copy of a list as it stands, and a name the operator
    removed must not keep counting as in the перечень. Only rows inside this one
    snapshot are touched — the downloaded list and the operator's own tables are not.
    """
    result = TableSyncResult(received=len(records))
    now = datetime.now(UTC)
    snapshot = session.scalar(
        select(RosfinmonitoringSnapshotRecord)
        .where(RosfinmonitoringSnapshotRecord.source_url == RFM_SOURCE_URL)
        .order_by(RosfinmonitoringSnapshotRecord.snapshot_date.desc())
        .limit(1)
    )
    if snapshot is None:
        snapshot = RosfinmonitoringSnapshotRecord(
            snapshot_date=now,
            source_url=RFM_SOURCE_URL,
            content_hash=_rfm_content_hash(records),
            entry_count=0,
            fetched_at=now,
        )
        session.add(snapshot)
        session.flush()
    snapshot_id = int(snapshot.id)

    existing = {
        (str(row.full_name), row.birth_date): row
        for row in session.scalars(
            select(RosfinmonitoringEntryRecord).where(
                RosfinmonitoringEntryRecord.snapshot_id == snapshot_id
            )
        )
    }
    listed = _listed_records(records)
    written: set[tuple[str, datetime | None]] = set()
    for record in listed:
        full_name = record.text(*_NAME_FIELDS)
        if not full_name:
            result.errors += 1
            logger.warning("event=airtable_rfm_person_without_name record=%s", record.id)
            continue
        if not record.flag(*_ACTIVE_FIELDS, default=True):
            # Not in the list, so not an entry. It is not written as `removed` either:
            # the matcher has no notion of "was in the list and left", so such a row
            # would keep matching and go on counting as in the перечень.
            continue
        birth_date = _parse_date(record.text(*_BIRTH_DATE_FIELDS))
        key = (full_name, birth_date)
        if key in written:
            # The table makes this pair unique within a snapshot. One person in several
            # cases is the normal shape of a list exported from a base of cases, so this
            # is counted rather than reported per name.
            result.unchanged += 1
            continue
        written.add(key)
        normalized = _normalize_name(full_name)
        values: dict[str, Any] = {
            "full_name": full_name,
            "normalized_name": normalized,
            "matching_key": _create_matching_key(normalized),
            "status": "active" if record.flag(*_ACTIVE_FIELDS, default=True) else "removed",
            "birth_place": record.text("birth_place", "Birth place", "Место рождения") or None,
            "snils": record.text("snils", "SNILS", "СНИЛС") or None,
            "inn": record.text("inn", "INN", "ИНН") or None,
            "inclusion_reason": record.text("reason", "Reason", "Причина", "Основание") or None,
            "raw_data": {"airtable_record_id": record.id},
        }
        row = existing.get(key)
        if row is None:
            session.add(
                RosfinmonitoringEntryRecord(
                    snapshot_id=snapshot_id, birth_date=birth_date, **values
                )
            )
            result.created += 1
        elif _same(row, values):
            result.unchanged += 1
        else:
            _write(row, values)
            result.updated += 1

    snapshot.entry_count = len(written)
    snapshot.fetched_at = now
    if result.unchanged:
        logger.info(
            "event=airtable_rfm_person_repeated_cases entries=%d repeated_rows=%d",
            len(written),
            result.unchanged,
        )
    snapshot.content_hash = _rfm_content_hash(records)
    _drop_vanished(session, existing, written)
    session.commit()
    return result


def _drop_vanished(
    session: Session,
    existing: Mapping[tuple[str, datetime | None], RosfinmonitoringEntryRecord],
    kept: set[tuple[str, datetime | None]],
) -> None:
    """Entries of this snapshot whose Airtable record is gone.

    Only what this one snapshot holds and no record stands for is removed: the
    downloaded list and the operator's own tables are not touched.
    """
    stale = [row.id for key, row in existing.items() if key not in kept]
    if not stale:
        return
    session.execute(
        delete(RosfinmonitoringEntryRecord).where(RosfinmonitoringEntryRecord.id.in_(stale))
    )


def _listed_records(records: Sequence[AirtableRecord]) -> list[AirtableRecord]:
    """The records that name someone in the list, one per person.

    A list exported from a base that tracks cases per person carries the same person
    several times, and a person whose birth date is filled in one case and left blank in
    another arrives as two entries under one name — the second, dateless one, is exactly
    the row that cannot tell a person from their namesake. Where a name appears both
    with a date and without, only the dated row is kept.

    A name that is *only* ever dateless is kept as it stands: dropping it would lose a
    person from the list altogether, which is worse than a weak entry.
    """
    names: set[str] = set()
    dated: set[str] = set()
    for record in records:
        if not record.flag(*_ACTIVE_FIELDS, default=True):
            continue
        name = record.text(*_NAME_FIELDS)
        if not name:
            continue
        names.add(name)
        if _parse_date(record.text(*_BIRTH_DATE_FIELDS)) is not None:
            dated.add(name)
    kept: list[AirtableRecord] = []
    folded = 0
    for record in records:
        if not record.flag(*_ACTIVE_FIELDS, default=True):
            continue
        name = record.text(*_NAME_FIELDS)
        if not name:
            kept.append(record)
            continue
        if name in dated and _parse_date(record.text(*_BIRTH_DATE_FIELDS)) is None:
            folded += 1
            continue
        kept.append(record)
    if folded:
        logger.info("event=airtable_rfm_person_undated_folded count=%d", folded)
    return kept


def _base_url_key(url: str) -> str:
    """One spelling of an address: case and a trailing slash are not a difference."""
    return url.strip().rstrip("/").lower()


def _rfm_content_hash(records: Sequence[AirtableRecord]) -> str:
    """A hash of the whole list, order-independent: the same records in any order are
    the same list, so a re-run over an untouched Airtable produces the same hash."""
    parts = sorted(
        "|".join(f"{field_name}={value!r}" for field_name, value in sorted(record.fields.items()))
        for record in records
    )
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
