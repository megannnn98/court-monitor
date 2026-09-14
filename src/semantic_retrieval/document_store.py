"""PostgreSQL storage of semantic documents and lexical entity retrieval.

`semantic_documents` is derived data: it can always be rebuilt from persons
and events. Lexical retrieval runs over the same text the embedder sees, so
lexical and dense backends are compared on equal input.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import SemanticDocumentRecord
from semantic_retrieval.models import (
    RetrievalBackend,
    RetrievalEntityType,
    RetrievalHit,
    RetrievalQuery,
    RetrievalResult,
    SemanticDocument,
)

logger = logging.getLogger("semantic_retrieval")

_WORD = re.compile(r"\w+", re.UNICODE)


@dataclass(frozen=True)
class StoredDocumentState:
    content_hash: str
    representation_version: int
    # A vector for exactly this content has been upserted.
    indexed: bool


class SqlAlchemySemanticDocumentRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def get_states(
        self, entity_type: RetrievalEntityType, entity_ids: Sequence[int]
    ) -> dict[int, StoredDocumentState]:
        if not entity_ids:
            return {}
        with self._session_factory() as session:
            rows = session.execute(
                select(
                    SemanticDocumentRecord.entity_id,
                    SemanticDocumentRecord.content_hash,
                    SemanticDocumentRecord.representation_version,
                    SemanticDocumentRecord.indexed_at,
                ).where(
                    SemanticDocumentRecord.entity_type == entity_type.value,
                    SemanticDocumentRecord.entity_id.in_(entity_ids),
                )
            ).all()
        return {
            entity_id: StoredDocumentState(content_hash, version, indexed_at is not None)
            for entity_id, content_hash, version, indexed_at in rows
        }

    def upsert(self, documents: Sequence[SemanticDocument]) -> None:
        """Insert or update; a changed hash or version clears `indexed_at`."""
        if not documents:
            return
        statement = insert(SemanticDocumentRecord).values(
            [
                {
                    "entity_type": document.entity_type.value,
                    "entity_id": document.entity_id,
                    "representation_version": document.representation_version,
                    "content_hash": document.content_hash,
                    "text": document.text,
                    "source_updated_at": document.source_updated_at,
                }
                for document in documents
            ]
        )
        excluded = statement.excluded
        unchanged = (SemanticDocumentRecord.content_hash == excluded.content_hash) & (
            SemanticDocumentRecord.representation_version == excluded.representation_version
        )
        statement = statement.on_conflict_do_update(
            constraint="uq_semantic_documents_entity",
            set_={
                "representation_version": excluded.representation_version,
                "content_hash": excluded.content_hash,
                "text": excluded.text,
                "source_updated_at": excluded.source_updated_at,
                "updated_at": func.now(),
                "indexed_at": case((unchanged, SemanticDocumentRecord.indexed_at), else_=None),
            },
        )
        with self._session_factory() as session:
            session.execute(statement)
            session.commit()

    def mark_indexed(
        self, entity_type: RetrievalEntityType, entity_ids: Sequence[int], indexed_at: datetime
    ) -> None:
        if not entity_ids:
            return
        with self._session_factory() as session:
            session.execute(
                update(SemanticDocumentRecord)
                .where(
                    SemanticDocumentRecord.entity_type == entity_type.value,
                    SemanticDocumentRecord.entity_id.in_(entity_ids),
                )
                .values(indexed_at=indexed_at)
            )
            session.commit()

    def clear_indexed(self, entity_type: RetrievalEntityType) -> None:
        with self._session_factory() as session:
            session.execute(
                update(SemanticDocumentRecord)
                .where(SemanticDocumentRecord.entity_type == entity_type.value)
                .values(indexed_at=None)
            )
            session.commit()

    def delete(self, entity_type: RetrievalEntityType, entity_ids: Sequence[int]) -> None:
        if not entity_ids:
            return
        with self._session_factory() as session:
            session.execute(
                delete(SemanticDocumentRecord).where(
                    SemanticDocumentRecord.entity_type == entity_type.value,
                    SemanticDocumentRecord.entity_id.in_(entity_ids),
                )
            )
            session.commit()

    def list_entity_ids(self, entity_type: RetrievalEntityType) -> list[int]:
        with self._session_factory() as session:
            return list(
                session.scalars(
                    select(SemanticDocumentRecord.entity_id)
                    .where(SemanticDocumentRecord.entity_type == entity_type.value)
                    .order_by(SemanticDocumentRecord.entity_id)
                ).all()
            )

    def get_texts(
        self, entity_type: RetrievalEntityType, entity_ids: Sequence[int]
    ) -> dict[int, str]:
        if not entity_ids:
            return {}
        with self._session_factory() as session:
            rows = session.execute(
                select(SemanticDocumentRecord.entity_id, SemanticDocumentRecord.text).where(
                    SemanticDocumentRecord.entity_type == entity_type.value,
                    SemanticDocumentRecord.entity_id.in_(entity_ids),
                )
            ).all()
        return {entity_id: text for entity_id, text in rows}


def or_tsquery_text(text: str) -> str | None:
    """`w1 | w2 | …` from word characters only (safe tsquery syntax), or None.

    OR instead of websearch AND: an entity matching part of a descriptive
    query is still a lexical candidate.
    """
    words = sorted({word.lower() for word in _WORD.findall(text) if len(word) > 1})
    return " | ".join(words) or None


class PostgresLexicalEntityRetriever:
    """Full-text retrieval over `semantic_documents` (Russian stemming, ts_rank_cd)."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        result = RetrievalResult(entity_type=query.entity_type, backend=RetrievalBackend.LEXICAL)
        tsquery_text = or_tsquery_text(query.text)
        if tsquery_text is None:
            return result

        tsquery = func.to_tsquery("russian", tsquery_text)
        score = func.ts_rank_cd(SemanticDocumentRecord.search_vector, tsquery).label("score")
        statement = (
            select(SemanticDocumentRecord.entity_id, score)
            .where(
                SemanticDocumentRecord.entity_type == query.entity_type.value,
                SemanticDocumentRecord.search_vector.op("@@")(tsquery),
            )
            .order_by(score.desc(), SemanticDocumentRecord.entity_id)
            .limit(query.limit)
        )
        if query.filters.entity_ids is not None:
            statement = statement.where(
                SemanticDocumentRecord.entity_id.in_(query.filters.entity_ids)
            )

        with self._session_factory() as session:
            rows = session.execute(statement).all()
        result.hits = [
            RetrievalHit(
                entity_type=query.entity_type,
                entity_id=entity_id,
                score=float(row_score),
                backend=RetrievalBackend.LEXICAL,
                rank=rank,
            )
            for rank, (entity_id, row_score) in enumerate(rows, start=1)
        ]
        logger.info("lexical_retrieval entity_type=%s count=%d", query.entity_type.value, len(rows))
        return result
