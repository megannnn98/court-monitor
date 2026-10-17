"""Which documents of a source PostgreSQL already holds."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import Source, SourceDocument


class SqlAlchemyStoredDocuments:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def known_external_ids(self, *, source_base_url: str, external_ids: Sequence[str]) -> set[str]:
        if not external_ids:
            return set()
        query = (
            select(SourceDocument.external_id)
            .join(Source, SourceDocument.source_id == Source.id)
            .where(
                Source.base_url == source_base_url,
                SourceDocument.external_id.in_(list(external_ids)),
            )
        )
        with self._session_factory() as session:
            return set(session.scalars(query).all())
