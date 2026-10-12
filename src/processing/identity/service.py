"""The public API of identity: which mentions are one human.

The canonical identity is the entity group (ADR 0024): the rules of `grouping` join
mentions, a model names each group in the nominative (`normalizer`), and a person's
decisions are applied (`disputes`, `overrides`, `removals`). Callers outside
`processing.identity` use this module and nothing beside it; a boundary test holds the
console and the command line to that.

Reading and deciding happen in the caller's transaction, so a method takes the session
it works in. The rebuild owns its transaction and takes none.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping

from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import EntityGroupRecord, EntityRemovalRecord
from processing.identity import disputes, overrides, removals
from processing.identity.collector import CollectResult, EntityCollector
from processing.identity.disputes import (
    BY_REGION,
    BY_RF,
    DIFFERENT,
    PATRONYMIC,
    REGION,
    SAME,
    SIMILAR,
    EntityRef,
    KeyIndex,
    Pair,
)
from processing.identity.grouping import split_key
from processing.identity.normalizer import NameNormalizer, name_normalizer_from_env
from processing.identity.overrides import MANUAL

__all__ = [
    "BY_REGION",
    "BY_RF",
    "DIFFERENT",
    "MANUAL",
    "PATRONYMIC",
    "REGION",
    "SAME",
    "SIMILAR",
    "CollectResult",
    "EntityRef",
    "IdentityService",
    "KeyIndex",
    "Pair",
    "identity_service_from_env",
    "key_region",
]


def key_region(key: str) -> str | None:
    """The region a group's key carries, when a registry card told it."""
    return split_key(key)[1]


class IdentityService:
    def __init__(self, *, normalizer: NameNormalizer | None = None) -> None:
        # The model only names a group the rules made; without one the rules' name stays.
        self._normalizer = normalizer

    @property
    def names_by_model(self) -> bool:
        return self._normalizer is not None

    def rebuild_groups(
        self,
        session_factory: sessionmaker[Session],
        *,
        on_stage: Callable[[str], None] = lambda _stage: None,
    ) -> CollectResult:
        """Rebuild every entity group from the mentions, in one transaction."""
        return EntityCollector(
            session_factory, on_stage=on_stage, normalizer=self._normalizer
        ).run()

    def disputed_pairs(self, session: Session, entities: Iterable[EntityRef]) -> list[Pair]:
        """The pairs that may be one person and nobody decided, most mentioned first."""
        return disputes.find_pairs(entities, disputes.decided_pairs(session))

    def decide_pair(
        self, session: Session, first_key: str, second_key: str, decision: str
    ) -> int | None:
        """Record a person's decision; «same» merges at once. The merged group's id."""
        return disputes.decide(session, first_key, second_key, decision)

    def merge_confirmed_pairs(self, session: Session, *, listed_level: str) -> Counter[str]:
        """Merge the pairs the Rosfinmonitoring list or a registry region confirms."""
        return disputes.merge_clear_pairs(session, listed_level=listed_level)

    def decision_counts(self, session: Session) -> Counter[str]:
        return disputes.decision_counts(session)

    def reset_decisions(self, session: Session) -> int:
        return disputes.reset_decisions(session)

    def correct_name(self, session: Session, group: EntityGroupRecord, name: str) -> str:
        """A person's correction of a group's name, kept across rebuilds."""
        return overrides.correct_name(session, group, name)

    def remove_group(self, session: Session, group: EntityGroupRecord) -> None:
        removals.remove(session, group)

    def restore_group(self, session: Session, key: str) -> bool:
        return removals.restore(session, key)

    def removed_groups(self, session: Session) -> list[EntityRemovalRecord]:
        return removals.removals(session)


def identity_service_from_env(env: Mapping[str, str] | None = None) -> IdentityService:
    return IdentityService(normalizer=name_normalizer_from_env(env))
