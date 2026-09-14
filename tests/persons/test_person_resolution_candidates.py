"""ER v2 candidate generation on PostgreSQL (pg_trgm) and a fake semantic retriever."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session, sessionmaker
from support.person_resolution_fixtures import matching_key, seed_person
from support.semantic_fakes import StaticRetriever

from db.orm_models import PersonRecord
from persons.models import PersonStatus
from persons.resolution.candidates import (
    CandidateConfig,
    CompositeCandidateGenerator,
    ExactKeyCandidateGenerator,
    SemanticCandidateGenerator,
    TrigramCandidateGenerator,
)
from persons.resolution.models import (
    CandidateSource,
    PersonIdentityInput,
    SemanticSourceStatus,
)
from semantic_retrieval.models import RetrievalBackend, RetrievalUnavailableError


def _identity(name: str) -> PersonIdentityInput:
    return PersonIdentityInput(name=name, matching_key=matching_key(name))


def _ids(candidates) -> list[int]:  # type: ignore[no-untyped-def]
    return [candidate.person_id for candidate in candidates]


def test_exact_key_candidates_come_from_names_and_aliases(
    session_factory: sessionmaker[Session],
) -> None:
    by_name = seed_person(session_factory, "Иван Иванов")
    by_alias = seed_person(session_factory, "Иван Сергеевич Петров", aliases=("Ваня Петров",))
    seed_person(session_factory, "Пётр Сидоров")

    with session_factory() as session:
        generator = ExactKeyCandidateGenerator()
        name_hits = generator.generate(_identity("Иван Иванов"), limit=10, session=session)
        alias_hits = generator.generate(_identity("Ваня Петров"), limit=10, session=session)

    assert _ids(name_hits) == [by_name]
    assert name_hits[0].sources == [CandidateSource.EXACT_KEY]
    assert _ids(alias_hits) == [by_alias]
    assert alias_hits[0].aliases == ["Ваня Петров"]
    assert alias_hits[0].sources == [CandidateSource.ALIAS]


def test_exact_key_returns_every_active_namesake_in_id_order(
    session_factory: sessionmaker[Session],
) -> None:
    namesakes = [seed_person(session_factory, "Алексей Сергеевич Иванов") for _ in range(3)]
    seed_person(session_factory, "Алексей Иванов")

    with session_factory() as session:
        hits = ExactKeyCandidateGenerator().generate(
            _identity("Алексей Сергеевич Иванов"), limit=10, session=session
        )
        composite = CompositeCandidateGenerator(
            [TrigramCandidateGenerator(), ExactKeyCandidateGenerator()]
        ).generate(_identity("Алексей Сергеевич Иванов"), limit=10, session=session)

    assert _ids(hits) == sorted(namesakes)
    assert all(hit.sources == [CandidateSource.EXACT_KEY] for hit in hits)
    # Exact-key candidates lead the merged pool, one entry per person, id order on ties.
    assert _ids(composite.candidates)[:3] == sorted(namesakes)
    assert len(set(_ids(composite.candidates))) == len(composite.candidates)


def test_trigram_finds_reordered_typo_and_initial_forms(
    session_factory: sessionmaker[Session],
) -> None:
    ivanov = seed_person(session_factory, "Иван Иванович Иванов")
    petrov = seed_person(session_factory, "Александр Петров")
    seed_person(session_factory, "Мария Кузнецова")

    with session_factory() as session:
        generator = TrigramCandidateGenerator()
        reordered = generator.generate(_identity("Иванов Иван Иванович"), limit=10, session=session)
        initials = generator.generate(_identity("И. И. Иванов"), limit=10, session=session)
        typo = generator.generate(_identity("Александр Пертров"), limit=10, session=session)
        unrelated = generator.generate(_identity("Ольга Смирнова"), limit=10, session=session)

    assert ivanov in _ids(reordered)
    assert ivanov in _ids(initials)
    assert petrov in _ids(typo)
    assert unrelated == []
    assert all(c.trigram_similarity is not None for c in reordered)


def test_trigram_uses_aliases_and_skips_merged_persons(
    session_factory: sessionmaker[Session],
) -> None:
    with_alias = seed_person(session_factory, "Иван Сергеевич Петров", aliases=("Ваня Петров",))
    merged = seed_person(session_factory, "Ваня Петров")
    with session_factory.begin() as session:
        session.get_one(PersonRecord, merged).status = PersonStatus.MERGED.value

    with session_factory() as session:
        found = TrigramCandidateGenerator().generate(
            _identity("Ваня Петров"), limit=10, session=session
        )

    assert _ids(found) == [with_alias]


def test_candidate_limit_and_deterministic_order(session_factory: sessionmaker[Session]) -> None:
    for given in ("Иван", "Илья", "Игорь", "Олег", "Павел"):
        seed_person(session_factory, f"{given} Иванов")

    with session_factory() as session:
        generator = TrigramCandidateGenerator()
        first = generator.generate(_identity("Иван Иванов"), limit=3, session=session)
        second = generator.generate(_identity("Иван Иванов"), limit=3, session=session)

    assert len(first) == 3
    assert _ids(first) == _ids(second)
    scores = [c.trigram_similarity or 0.0 for c in first]
    assert scores == sorted(scores, reverse=True)


def test_semantic_candidates_use_their_own_threshold(
    session_factory: sessionmaker[Session],
) -> None:
    close = seed_person(session_factory, "Иван Иванов")
    far = seed_person(session_factory, "Пётр Сидоров")
    retriever = StaticRetriever(
        RetrievalBackend.HYBRID, [close, far], dense_scores={close: 0.9, far: 0.5}
    )

    with session_factory() as session:
        found = SemanticCandidateGenerator(retriever, min_score=0.6).generate(
            _identity("Иван Иванов"), limit=10, session=session
        )

    assert _ids(found) == [close]
    assert found[0].semantic_similarity == 0.9
    assert found[0].sources == [CandidateSource.SEMANTIC]


def test_composite_merges_sources_and_reports_semantic_status(
    session_factory: sessionmaker[Session],
) -> None:
    person = seed_person(session_factory, "Иван Иванов")
    semantic = SemanticCandidateGenerator(
        StaticRetriever(RetrievalBackend.HYBRID, [person], dense_scores={person: 0.8})
    )
    composite = CompositeCandidateGenerator(
        [ExactKeyCandidateGenerator(), TrigramCandidateGenerator(), semantic]
    )

    with session_factory() as session:
        result = composite.generate(_identity("Иван Иванов"), limit=10, session=session)

    (candidate,) = result.candidates
    assert set(candidate.sources) == {
        CandidateSource.EXACT_KEY,
        CandidateSource.TRIGRAM,
        CandidateSource.SEMANTIC,
    }
    assert candidate.semantic_similarity == 0.8
    assert candidate.trigram_similarity == 1.0
    assert result.semantic_source is SemanticSourceStatus.OK


def test_semantic_outage_keeps_lexical_candidates(session_factory: sessionmaker[Session]) -> None:
    person = seed_person(session_factory, "Иван Иванов")
    broken = SemanticCandidateGenerator(
        StaticRetriever(RetrievalBackend.DENSE, error=RetrievalUnavailableError("down"))
    )
    composite = CompositeCandidateGenerator([TrigramCandidateGenerator(), broken])

    with session_factory() as session:
        result = composite.generate(_identity("Иван Иванов"), limit=10, session=session)

    assert _ids(result.candidates) == [person]
    assert result.semantic_source is SemanticSourceStatus.UNAVAILABLE


def test_candidate_limit_below_two_is_rejected() -> None:
    # One candidate could hide a namesake from the decision policy.
    with pytest.raises(ValueError, match="between 2 and"):
        CandidateConfig(candidate_limit=1)
    with pytest.raises(ValueError, match="between 2 and"):
        CandidateConfig.from_env({"ER_CANDIDATE_LIMIT": "1"})
    assert CandidateConfig(candidate_limit=2).candidate_limit == 2
