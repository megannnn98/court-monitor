"""Writing the four Airtable lists into PostgreSQL, one table at a time.

Each function is idempotent: it matches rows by the Airtable record id stored in
`external_id`, and reports what it did instead of rebuilding the table. A record that
disappeared from Airtable is never deleted here — a list a person curates is theirs to
remove, not the sync's.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from airtable.client import AirtableRecord
from airtable.models import TableSyncResult
from airtable.replace import UntrustedExport, drop_vanished, removal_is_safe
from db.orm_models import (
    AirtableKnownPersonRecord,
    ExcludedPersonRecord,
    Source,
)
from extraction.normalizers import RuleBasedMentionNormalizer

logger = logging.getLogger("airtable")

# The snapshot a sync of `rfm_persons` writes into. It is an ordinary snapshot, but not
# the list: `snapshot_lookup.latest_imported_snapshot` skips it, and a name in it alone
# is a probable match (`rosfinmonitoring.probable`).
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


def sync_known_persons(
    session: Session,
    records: Sequence[AirtableRecord],
    *,
    replace: bool = False,
) -> TableSyncResult:
    """Upsert the people we already have on file, and — when the list is a copy of an
    Airtable view — replace it whole.

    `replace` is off by default and is turned on only by the share-link reader, which is
    the one source that is a copy of somebody else's table rather than a record of the
    operator's decisions. With it on, rows the export no longer names go, in this same
    transaction, and only if the export is complete enough to be believed — see
    `airtable.replace`.
    """
    result = TableSyncResult(received=len(records))
    # Counted before anything is written. `session.add` is flushed by the next query, so
    # counting afterwards would include the rows this very run has just added, and «what
    # the list held before» would silently mean «what it holds now».
    previous_rows = 0
    if replace:
        previous_rows = int(
            session.scalar(select(func.count()).select_from(AirtableKnownPersonRecord)) or 0
        )
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
    if replace:
        _replace_whole(session, AirtableKnownPersonRecord, records, result, previous_rows)
    session.commit()
    return result


def _replace_whole(
    session: Session,
    model: type[Any],
    records: Sequence[AirtableRecord],
    result: TableSyncResult,
    previous_rows: int,
) -> None:
    """Drop what the export no longer holds, or refuse the whole export.

    The check runs first, before anything is written, and a refusal takes the writes with
    it. An export too small to believe is not a smaller version of the list — it is not
    the list at all, and writing the handful of rows it did bring would leave the table
    holding both: the old list plus whatever the broken link happened to return. The
    promise is that the list stands whole, so it stands whole, and the report says why
    nothing changed.
    """
    keep = {record.id for record in records}
    safe, reason = removal_is_safe(len(keep), previous_rows)
    if not safe:
        raise UntrustedExport(reason, received=len(keep), kept=previous_rows)
    result.removed = drop_vanished(session, model, keep)


def sync_officials(session: Session, records: Sequence[AirtableRecord]) -> TableSyncResult:
    """Upsert the officials, and anyone else who must never become a target figurant."""
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
            logger.warning("event=airtable_official_without_name record=%s", record.id)
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


def _base_url_key(url: str) -> str:
    """One spelling of an address: case and a trailing slash are not a difference."""
    return url.strip().rstrip("/").lower()
