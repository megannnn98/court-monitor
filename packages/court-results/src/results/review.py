"""Operator review read models composed from the owning packages' bulk APIs."""

from collections.abc import Collection
from dataclasses import dataclass

from sqlalchemy.orm import Session

from analysis.read import verdict_reasons
from analysis.rosfinmonitoring.read import entry_matches
from processing.read import groups, references_by_mentions


@dataclass(frozen=True)
class EntryPersonLink:
    key: str
    name: str
    level: str


def rf_entry_links(session: Session, entry_ids: Collection[int]) -> dict[int, EntryPersonLink]:
    """Best existing group per displayed list entry, from two bulk owner reads."""
    matches = entry_matches(session, entry_ids)
    entities = groups(session, {match.group_id for match in matches})
    found: dict[int, EntryPersonLink] = {}
    for match in matches:
        if match.entry_id not in found and (entity := entities.get(match.group_id)) is not None:
            found[match.entry_id] = EntryPersonLink(entity.key, entity.name, match.level)
    return found


def politics_review(session: Session, *, verdict: str, limit: int) -> list[tuple[str, str, str]]:
    """(key, name, reason) for verdict review, most mentioned first, in two queries."""
    reasons = verdict_reasons(session, verdict=verdict)
    return [
        (group.key, group.name, reasons[group.id])
        for group in references_by_mentions(session, reasons.keys(), limit=limit)
    ]
