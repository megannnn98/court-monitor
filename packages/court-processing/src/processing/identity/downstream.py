"""What identity needs of the stages after it, and what it tells them (ADR 0031).

Identity is upstream of the analysis: it may not import it, nor read or write its
tables. Two things cross anyway, and this contract, owned here, carries them:

- an operator's identification of an unnamed figurant («17-летний житель Тюмени» is this
  person) is evidence the rebuild of entity groups joins to that person;
- a group that stops being a figurant, or is merged into another, changes what the
  analysis keeps of it (its politics verdict, its list matches), in the same transaction.

The analysis implements it (`analysis.service.GroupFollowers`); the composition root
hands it to `IdentityService` and to the commands that change a group.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from sqlalchemy.orm import Session

# The resolutions that name a real person: the ones identity joins to a group.
IDENTIFYING_RESOLUTIONS = ("rf_entry", "existing_person", "supplied_name")


@dataclass(frozen=True)
class IdentifiedFigurant:
    """An unnamed figurant an operator identified, as the rebuild of groups reads it."""

    key: str
    resolution: str
    normalized_name: str | None
    existing_person_key: str | None
    gender: str | None
    article_id: int
    event_type: str
    quote: str
    # The Criminal Code articles of the figurant's event.
    articles: tuple[str, ...]
    published_at: datetime | None


@dataclass(frozen=True)
class FigurantEvidence:
    """An unnamed figurant joined to a group, as the group's evidence shows it: where its
    article names it, and the operator's resolution of it as it stands now."""

    key: str
    article_id: int
    start_offset: int
    end_offset: int
    quote: str
    resolution: str
    rf_name: str | None
    rf_birth_date: date | None
    decided_at: datetime


class IdentityDownstream(Protocol):
    def identified_figurants(self, session: Session) -> list[IdentifiedFigurant]:
        """Every unnamed figurant identified as a real person (`IDENTIFYING_RESOLUTIONS`)."""
        ...

    def figurants(self, session: Session, keys: Collection[str]) -> dict[str, FigurantEvidence]:
        """The figurants asked for that have a resolution, whatever it is now."""
        ...

    def figurants_in(
        self, session: Session, articles: Collection[int]
    ) -> dict[str, FigurantEvidence]:
        """The figurants of the articles asked for that have a resolution, whatever it is."""
        ...

    def role_withdrawn(self, session: Session, group_id: int) -> None:
        """The group is no longer a figurant: what put it on «Результат» goes."""
        ...

    def groups_merged(self, session: Session, *, keep: int, drop: int) -> None:
        """`drop` was merged into `keep`: what the analysis keeps of `drop` is `keep`'s now."""
        ...
