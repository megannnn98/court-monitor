"""Commands on the operator's exclusion list, in the caller's transaction."""

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from processing.storage.models import ExcludedPersonRecord


@dataclass(frozen=True)
class OfficialDetails:
    external_id: str
    full_name: str
    normalized_name: str
    category: str
    reason: str | None
    active: bool


@dataclass(frozen=True)
class OfficialRefresh:
    created: int
    updated: int
    unchanged: int


def refresh_officials(session: Session, updates: Sequence[OfficialDetails]) -> OfficialRefresh:
    """Upsert valid external list entries without replacing manual exclusions.

    No commit: synchronizing this list keeps its existing transaction boundary.
    """
    existing = {
        str(row.external_id): row
        for row in session.scalars(
            select(ExcludedPersonRecord).where(
                ExcludedPersonRecord.external_id.in_({update.external_id for update in updates})
            )
        )
    }
    created = updated = unchanged = 0
    for update in updates:
        values = {
            "full_name": update.full_name,
            "normalized_name": update.normalized_name,
            "category": update.category,
            "reason": update.reason,
            "active": update.active,
        }
        row = existing.get(update.external_id)
        if row is None:
            session.add(ExcludedPersonRecord(external_id=update.external_id, **values))
            created += 1
        elif all(getattr(row, name) == value for name, value in values.items()):
            unchanged += 1
        else:
            for name, value in values.items():
                setattr(row, name, value)
            updated += 1
    return OfficialRefresh(created, updated, unchanged)
