"""semantic_documents repository and lexical entity retrieval (PostgreSQL)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker

from semantic_retrieval.document_store import (
    PostgresLexicalEntityRetriever,
    SqlAlchemySemanticDocumentRepository,
)
from semantic_retrieval.documents import compute_content_hash
from semantic_retrieval.models import (
    RetrievalBackend,
    RetrievalEntityType,
    RetrievalFilters,
    RetrievalQuery,
    SemanticDocument,
)

PERSON = RetrievalEntityType.PERSON
EVENT = RetrievalEntityType.EVENT


def _document(
    entity_id: int, text: str, *, entity_type: RetrievalEntityType = PERSON
) -> SemanticDocument:
    return SemanticDocument(
        entity_type=entity_type,
        entity_id=entity_id,
        text=text,
        representation_version=1,
        content_hash=compute_content_hash(entity_type, 1, text),
    )


def test_upsert_tracks_changes_and_indexed_state(session_factory: sessionmaker[Session]) -> None:
    repository = SqlAlchemySemanticDocumentRepository(session_factory)
    repository.upsert([_document(1, "первый"), _document(2, "второй")])
    repository.mark_indexed(PERSON, [1, 2], datetime(2026, 9, 14, tzinfo=UTC))

    repository.upsert([_document(1, "первый"), _document(2, "второй изменён")])
    states = repository.get_states(PERSON, [1, 2, 3])

    assert set(states) == {1, 2}
    assert states[1].indexed is True  # unchanged text keeps the indexed mark
    assert states[2].indexed is False  # changed text must be re-embedded
    assert states[2].content_hash == compute_content_hash(PERSON, 1, "второй изменён")


def test_entity_types_are_separate_and_delete_removes_rows(
    session_factory: sessionmaker[Session],
) -> None:
    repository = SqlAlchemySemanticDocumentRepository(session_factory)
    repository.upsert([_document(1, "персона"), _document(1, "событие", entity_type=EVENT)])

    repository.delete(PERSON, [1])

    assert repository.list_entity_ids(PERSON) == []
    assert repository.list_entity_ids(EVENT) == [1]
    assert repository.get_texts(EVENT, [1]) == {1: "событие"}


def test_clear_indexed_marks_every_document_of_a_type_for_reembedding(
    session_factory: sessionmaker[Session],
) -> None:
    repository = SqlAlchemySemanticDocumentRepository(session_factory)
    repository.upsert([_document(1, "a"), _document(2, "b", entity_type=EVENT)])
    repository.mark_indexed(PERSON, [1], datetime(2026, 9, 14, tzinfo=UTC))
    repository.mark_indexed(EVENT, [2], datetime(2026, 9, 14, tzinfo=UTC))

    repository.clear_indexed(PERSON)

    assert repository.get_states(PERSON, [1])[1].indexed is False
    assert repository.get_states(EVENT, [2])[2].indexed is True


def _lexical(session_factory: sessionmaker[Session]) -> PostgresLexicalEntityRetriever:
    repository = SqlAlchemySemanticDocumentRepository(session_factory)
    repository.upsert(
        [
            _document(1, "Персона: Иван. Задержан на одиночном пикете против войны."),
            _document(2, "Персона: Анна. Оштрафована за публикацию во ВКонтакте."),
            _document(3, "Персона: Пётр. Пикет у здания суда, задержан полицией."),
            _document(4, "Событие: задержание на пикете.", entity_type=EVENT),
        ]
    )
    return PostgresLexicalEntityRetriever(session_factory)


def test_lexical_retrieval_matches_word_forms_and_ranks_by_overlap(
    session_factory: sessionmaker[Session],
) -> None:
    result = _lexical(session_factory).retrieve(
        RetrievalQuery(text="задержания на пикетах", entity_type=PERSON, limit=10)
    )

    assert result.backend is RetrievalBackend.LEXICAL
    # Any query word counts (OR); Russian stemming matches "пикетах" to "пикете".
    assert set(result.entity_ids) == {1, 3}
    assert [hit.rank for hit in result.hits] == [1, 2]
    assert all(hit.entity_type is PERSON for hit in result.hits)


def test_lexical_retrieval_respects_limit_and_entity_id_filter(
    session_factory: sessionmaker[Session],
) -> None:
    retriever = _lexical(session_factory)

    limited = retriever.retrieve(RetrievalQuery(text="пикет", entity_type=PERSON, limit=1))
    filtered = retriever.retrieve(
        RetrievalQuery(
            text="пикет", entity_type=PERSON, limit=10, filters=RetrievalFilters(entity_ids=[3])
        )
    )

    assert len(limited.hits) == 1
    assert filtered.entity_ids == [3]


def test_lexical_retrieval_without_overlap_or_only_punctuation_is_empty(
    session_factory: sessionmaker[Session],
) -> None:
    retriever = _lexical(session_factory)

    assert (
        retriever.retrieve(RetrievalQuery(text="антивоенная позиция", entity_type=PERSON)).hits
        == []
    )
    assert retriever.retrieve(RetrievalQuery(text="!!! & | ???", entity_type=PERSON)).hits == []
