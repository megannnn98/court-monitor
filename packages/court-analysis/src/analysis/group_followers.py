"""The analysis's side of identity's contract (`processing.identity.downstream`): what it
keeps of an entity group follows the group, in the caller's transaction (ADR 0031)."""

from __future__ import annotations

import json

from sqlalchemy import delete, select, text, update
from sqlalchemy.orm import Session

from analysis.storage.models import (
    EntityGroupPoliticsRecord,
    EntityGroupRfMatchRecord,
    UnnamedFigurantRecord,
    UnnamedIdentityResolutionRecord,
)
from processing.identity.downstream import IDENTIFYING_RESOLUTIONS, IdentifiedFigurant

# One list entry matched twice: the stronger level stays.
_ONE_MATCH_PER_ENTRY = text(
    """
    DELETE FROM entity_group_rf_matches WHERE group_id = :keep AND id NOT IN (
        SELECT DISTINCT ON (entry_id) id FROM entity_group_rf_matches
        WHERE group_id = :keep ORDER BY entry_id, (level = 'full') DESC, id)
    """
)


def _as_text(article: object) -> str:
    """An element of the JSON list as `jsonb_array_elements_text` gives it; '' for null."""
    if article is None:
        return ""
    return article if isinstance(article, str) else json.dumps(article)


class GroupFollowers:
    """`processing.identity.downstream.IdentityDownstream`, over the analysis's tables."""

    def identified_figurants(self, session: Session) -> list[IdentifiedFigurant]:
        rows = session.execute(
            select(UnnamedIdentityResolutionRecord, UnnamedFigurantRecord)
            .join(
                UnnamedFigurantRecord,
                UnnamedFigurantRecord.key == UnnamedIdentityResolutionRecord.figurant_key,
            )
            .where(UnnamedIdentityResolutionRecord.resolution.in_(IDENTIFYING_RESOLUTIONS))
        ).all()
        return [
            IdentifiedFigurant(
                key=figurant.key,
                resolution=resolution.resolution,
                normalized_name=resolution.normalized_name,
                existing_person_key=resolution.existing_person_key,
                gender=figurant.gender,
                article_id=figurant.article_id,
                event_type=figurant.event_type,
                quote=figurant.quote,
                articles=tuple(_as_text(article) for article in figurant.articles or ()),
                published_at=figurant.published_at,
            )
            for resolution, figurant in rows
        ]

    def role_withdrawn(self, session: Session, group_id: int) -> None:
        session.execute(
            delete(EntityGroupPoliticsRecord).where(EntityGroupPoliticsRecord.group_id == group_id)
        )

    def groups_merged(self, session: Session, *, keep: int, drop: int) -> None:
        session.execute(
            update(EntityGroupRfMatchRecord)
            .where(EntityGroupRfMatchRecord.group_id == drop)
            .values(group_id=keep)
        )
        session.execute(_ONE_MATCH_PER_ENTRY, {"keep": keep})
