"""ER v2 applied to stored mentions on PostgreSQL: links, provenance, review, races."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from support.person_resolution_fixtures import seed_mentions, seed_person
from support.semantic_fakes import StaticRetriever

from db.orm_models import (
    EntityMentionRecord,
    PersonAliasRecord,
    PersonRecord,
    PersonResolutionDecisionRecord,
    ReviewRecordModel,
)
from extraction.resolution_service import ExtractionResolutionService
from persons.persistence import SqlAlchemyPersonPersistence
from persons.resolution.candidates import (
    CandidateConfig,
    CandidateGenerationResult,
    CompositeCandidateGenerator,
)
from persons.resolution.decision import PersonResolutionDecisionPolicy, ResolutionThresholds
from persons.resolution.factory import build_generators, build_person_resolution_service
from persons.resolution.models import PersonIdentityInput, PersonResolutionAction
from persons.resolution.service import (
    RESOLVER_VERSION,
    REVIEW_SUBJECT_TYPE,
    PersonResolutionEngine,
    PersonResolutionService,
)
from semantic_retrieval.models import RetrievalBackend, RetrievalUnavailableError

A = PersonResolutionAction


def _service(
    session_factory: sessionmaker[Session], env: dict[str, str] | None = None, **kwargs: object
) -> PersonResolutionService:
    return build_person_resolution_service(session_factory, env or {}, **kwargs)  # type: ignore[arg-type]


def _resolve(
    session_factory: sessionmaker[Session],
    surface: str,
    service: PersonResolutionService | None = None,
) -> tuple[int, PersonResolutionAction, int | None]:
    service = service or _service(session_factory)
    _, (mention_id,) = seed_mentions(session_factory, surface)
    with session_factory.begin() as session:
        mention = session.get_one(EntityMentionRecord, mention_id)
        outcome = service.resolve_mention(session, mention)
        assert outcome is not None
        return mention_id, outcome.action, outcome.person_id


def _decision(
    session_factory: sessionmaker[Session], mention_id: int
) -> PersonResolutionDecisionRecord:
    with session_factory() as session:
        return session.scalars(
            select(PersonResolutionDecisionRecord).where(
                PersonResolutionDecisionRecord.mention_id == mention_id
            )
        ).one()


def _count(session_factory: sessionmaker[Session], model: type) -> int:
    with session_factory() as session:
        return session.scalar(select(func.count()).select_from(model)) or 0


def test_unique_exact_candidate_auto_links_through_the_policy_with_provenance(
    session_factory: sessionmaker[Session],
) -> None:
    person = seed_person(session_factory, "Алексей Сергеевич Иванов")

    mention_id, action, linked = _resolve(session_factory, "Алексей Сергеевич Иванов")

    assert (action, linked) == (A.AUTO_LINK, person)
    decision = _decision(session_factory, mention_id)
    # No fast path: the exact key is a candidate source and a feature, the policy decides.
    assert decision.method == "er_v2"
    assert decision.resolver_version == RESOLVER_VERSION == "er-v2"
    assert decision.status == "applied"
    assert decision.selected_person_id == person
    assert decision.reasons == ["strong_unique_match"]
    (top,) = decision.candidates
    assert "exact_key" in top["candidate"]["sources"]
    assert top["features"]["exact_matching_key"] is True


def test_two_active_namesakes_go_to_review_never_the_lowest_id(
    session_factory: sessionmaker[Session],
) -> None:
    namesakes = [seed_person(session_factory, "Алексей Сергеевич Иванов") for _ in range(2)]

    mention_id, action, linked = _resolve(session_factory, "Алексей Сергеевич Иванов")

    assert (action, linked) == (A.REVIEW, None)
    decision = _decision(session_factory, mention_id)
    assert decision.selected_person_id is None
    assert {
        "multiple_exact_name_matches",
        "multiple_plausible_candidates",
        "low_decision_margin",
    } <= set(decision.reasons)
    assert sorted(c["candidate"]["person_id"] for c in decision.candidates) == sorted(namesakes)
    assert _count(session_factory, PersonRecord) == 2
    assert _count(session_factory, PersonAliasRecord) == 0


def test_reordered_name_of_two_namesakes_goes_to_review(
    session_factory: sessionmaker[Session],
) -> None:
    for _ in range(2):
        seed_person(session_factory, "Алексей Сергеевич Иванов")

    _, action, linked = _resolve(session_factory, "Иванов Алексей Сергеевич")

    assert (action, linked) == (A.REVIEW, None)


def test_semantic_similarity_does_not_break_a_namesake_tie(
    session_factory: sessionmaker[Session],
) -> None:
    first, second = (seed_person(session_factory, "Алексей Сергеевич Иванов") for _ in range(2))
    retriever = StaticRetriever(
        RetrievalBackend.HYBRID, [second, first], dense_scores={second: 0.99, first: 0.1}
    )
    service = _service(
        session_factory, {"ER_SEMANTIC_CANDIDATES": "1"}, semantic_retriever=retriever
    )

    mention_id, action, linked = _resolve(session_factory, "Алексей Сергеевич Иванов", service)

    assert (action, linked) == (A.REVIEW, None)
    scores = {
        c["candidate"]["person_id"]: c["score"]["resolution_score"]
        for c in _decision(session_factory, mention_id).candidates
    }
    assert scores[first] == scores[second]


def test_exact_candidate_with_a_weaker_competitor_auto_links(
    session_factory: sessionmaker[Session],
) -> None:
    full = seed_person(session_factory, "Глеб Андреевич Кравцов")
    seed_person(session_factory, "Глеб Кравцов")

    mention_id, action, linked = _resolve(session_factory, "Глеб Андреевич Кравцов")

    assert (action, linked) == (A.AUTO_LINK, full)
    decision = _decision(session_factory, mention_id)
    assert decision.decision_margin is not None and decision.decision_margin > 0.1


def test_reordered_full_name_auto_links_through_er_v2_and_promotes_alias(
    session_factory: sessionmaker[Session],
) -> None:
    person = seed_person(session_factory, "Иван Иванович Иванов")

    mention_id, action, linked = _resolve(session_factory, "Иванов Иван Иванович")

    assert (action, linked) == (A.AUTO_LINK, person)
    decision = _decision(session_factory, mention_id)
    assert decision.method == "er_v2"
    assert decision.resolution_score == 0.9
    assert "strong_unique_match" in decision.reasons
    top = decision.candidates[0]
    assert top["candidate"]["person_id"] == person
    assert top["features"]["order_differs"] is True
    with session_factory() as session:
        aliases = session.scalars(
            select(PersonAliasRecord.surface_text).where(PersonAliasRecord.person_id == person)
        ).all()
    assert "Иванов Иван Иванович" in aliases
    assert _count(session_factory, PersonRecord) == 1


def test_known_alias_from_another_article_is_reviewed_not_linked(
    session_factory: sessionmaker[Session],
) -> None:
    # A known alias without patronymic is still only a name: the same form may be a namesake.
    seed_person(session_factory, "Иван Сергеевич Петров", aliases=("Ваня Петров",))

    mention_id, action, linked = _resolve(session_factory, "Ваня Петров")

    assert (action, linked) == (A.REVIEW, None)
    assert "name_only_evidence" in _decision(session_factory, mention_id).reasons


def test_name_repeated_in_the_same_article_links_to_the_person_created_there(
    session_factory: sessionmaker[Session],
) -> None:
    run_id, _ = seed_mentions(session_factory, "Иван Фролов", "Иван Фролов")
    extraction = ExtractionResolutionService(
        persistence=SqlAlchemyPersonPersistence(session_factory),
        session_factory=session_factory,
        person_resolution=_service(session_factory),
    )

    stats = extraction.resolve_extraction_run(run_id)

    assert (stats.new_persons_created, stats.reviews_pending) == (1, 0)
    assert _count(session_factory, PersonRecord) == 1


def test_same_full_name_in_another_article_is_reviewed(
    session_factory: sessionmaker[Session],
) -> None:
    """Real-world validation v1: a different «Иван Фролов» was auto-linked by name alone."""
    seed_person(session_factory, "Иван Фролов")

    mention_id, action, linked = _resolve(session_factory, "Иван Фролов")

    assert (action, linked) == (A.REVIEW, None)
    decision = _decision(session_factory, mention_id)
    assert "name_only_evidence" in decision.reasons
    assert _count(session_factory, PersonRecord) == 1


def test_no_candidate_creates_a_new_person(session_factory: sessionmaker[Session]) -> None:
    seed_person(session_factory, "Мария Кузнецова")

    mention_id, action, linked = _resolve(session_factory, "Пётр Сидоров")

    assert action is A.CREATE_NEW and linked is not None
    decision = _decision(session_factory, mention_id)
    assert decision.selected_person_id == linked
    assert decision.status == "applied"
    assert _count(session_factory, PersonRecord) == 2


def test_ambiguous_initials_stay_unresolved_pending_review(
    session_factory: sessionmaker[Session],
) -> None:
    seed_person(session_factory, "Иван Иванов")
    seed_person(session_factory, "Илья Иванов")

    mention_id, action, linked = _resolve(session_factory, "И. Иванов")

    assert (action, linked) == (A.REVIEW, None)
    decision = _decision(session_factory, mention_id)
    assert decision.status == "pending_review"
    assert decision.selected_person_id is None
    assert {"initials_only", "multiple_plausible_candidates"} <= set(decision.reasons)
    with session_factory() as session:
        assert session.get_one(EntityMentionRecord, mention_id).person_id is None
        review = session.scalars(select(ReviewRecordModel)).one()
    assert (review.subject_type, review.subject_id, review.decision) == (
        REVIEW_SUBJECT_TYPE,
        decision.id,
        "pending",
    )
    # No placeholder Person, no alias from an initials form.
    assert _count(session_factory, PersonRecord) == 2
    assert _count(session_factory, PersonAliasRecord) == 0


def test_repeated_resolution_is_idempotent(session_factory: sessionmaker[Session]) -> None:
    seed_person(session_factory, "Иван Иванов")
    seed_person(session_factory, "Илья Иванов")
    run_id, _ = seed_mentions(session_factory, "И. Иванов", "Пётр Сидоров", "Иван Иванов")
    extraction = ExtractionResolutionService(
        persistence=SqlAlchemyPersonPersistence(session_factory),
        session_factory=session_factory,
        person_resolution=_service(session_factory),
    )

    first = extraction.resolve_extraction_run(run_id)
    counts = [
        _count(session_factory, m)
        for m in (
            PersonRecord,
            PersonResolutionDecisionRecord,
            ReviewRecordModel,
            PersonAliasRecord,
        )
    ]
    second = extraction.resolve_extraction_run(run_id)

    # «Иван Иванов» from another article is name-only evidence: reviewed, not linked.
    assert (first.mentions_resolved, first.new_persons_created, first.reviews_pending) == (1, 1, 2)
    assert second.new_persons_created == 0
    assert second.mentions_resolved == first.mentions_resolved
    assert counts == [
        _count(session_factory, m)
        for m in (
            PersonRecord,
            PersonResolutionDecisionRecord,
            ReviewRecordModel,
            PersonAliasRecord,
        )
    ]


def test_typo_match_neither_links_nor_promotes_the_typo_to_an_alias(
    session_factory: sessionmaker[Session],
) -> None:
    seed_person(session_factory, "Александр Сергеевич Петров")
    lenient = _service(
        session_factory, {"ER_AUTO_LINK_MIN_SCORE": "0.8", "ER_REVIEW_MIN_SCORE": "0.5"}
    )

    _, action, linked = _resolve(session_factory, "Александр Сергеевич Пертров", lenient)

    # Even with lowered thresholds a misspelled surname is not full identity evidence.
    assert (action, linked) == (A.REVIEW, None)
    assert _count(session_factory, PersonAliasRecord) == 0


def test_semantic_similarity_trap_does_not_link_different_names(
    session_factory: sessionmaker[Session],
) -> None:
    # Same kind of political story, different people: dense similarity is high.
    activist = seed_person(session_factory, "Иван Иванов")
    retriever = StaticRetriever(RetrievalBackend.HYBRID, [activist], dense_scores={activist: 0.97})
    service = _service(
        session_factory, {"ER_SEMANTIC_CANDIDATES": "1"}, semantic_retriever=retriever
    )

    mention_id, action, linked = _resolve(session_factory, "Пётр Сидоров", service)

    assert action is A.CREATE_NEW
    assert linked != activist
    candidate = _decision(session_factory, mention_id).candidates[0]
    assert candidate["candidate"]["sources"] == ["semantic"]
    assert candidate["features"]["semantic_similarity"] == 0.97


def test_semantic_outage_keeps_lexical_resolution_working(
    session_factory: sessionmaker[Session],
) -> None:
    person = seed_person(session_factory, "Иван Иванович Иванов")
    broken = StaticRetriever(RetrievalBackend.DENSE, error=RetrievalUnavailableError("down"))
    service = _service(session_factory, {"ER_SEMANTIC_CANDIDATES": "1"}, semantic_retriever=broken)

    mention_id, action, linked = _resolve(session_factory, "Иванов Иван Иванович", service)

    assert (action, linked) == (A.AUTO_LINK, person)
    assert _decision(session_factory, mention_id).semantic_source == "unavailable"


def test_similar_existing_persons_are_reviewed_not_merged(
    session_factory: sessionmaker[Session],
) -> None:
    first = seed_person(session_factory, "Иван Иванович Иванов")
    second = seed_person(session_factory, "Иванов Иван Иванович")

    mention_id, action, linked = _resolve(session_factory, "Иван Иванович Иваноф")

    assert (action, linked) == (A.REVIEW, None)
    reasons = set(_decision(session_factory, mention_id).reasons)
    assert "multiple_plausible_candidates" in reasons
    with session_factory() as session:
        statuses = session.scalars(
            select(PersonRecord.status).where(PersonRecord.id.in_([first, second]))
        ).all()
    assert statuses == ["active", "active"]


class _SlowGenerator(CompositeCandidateGenerator):
    """Widens the race window between candidate generation and person creation."""

    def generate(
        self, identity: PersonIdentityInput, *, limit: int, session: Session
    ) -> CandidateGenerationResult:
        result = super().generate(identity, limit=limit, session=session)
        time.sleep(0.4)
        return result


def _slow_service(persistence: SqlAlchemyPersonPersistence) -> PersonResolutionService:
    engine = PersonResolutionEngine(
        persistence=persistence,
        generator=_SlowGenerator(build_generators(CandidateConfig())),
        policy=PersonResolutionDecisionPolicy(ResolutionThresholds()),
    )
    return PersonResolutionService(engine=engine, persistence=persistence)


def _race(jobs: list[Callable[[], None]]) -> list[BaseException]:
    barrier = threading.Barrier(len(jobs))
    errors: list[BaseException] = []

    def wrap(job: Callable[[], None]) -> Callable[[], None]:
        def run() -> None:
            try:
                barrier.wait()
                job()
            except BaseException as exc:  # noqa: BLE001 - surfaced by the caller's assert
                errors.append(exc)

        return run

    threads = [threading.Thread(target=wrap(job)) for job in jobs]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return errors


def _concurrent_extraction_runs(session_factory: sessionmaker[Session], *surfaces: str) -> None:
    runs = [seed_mentions(session_factory, surface)[0] for surface in surfaces]
    persistence = SqlAlchemyPersonPersistence(session_factory)

    def job(run_id: int) -> Callable[[], None]:
        def run() -> None:
            ExtractionResolutionService(
                persistence=persistence,
                session_factory=session_factory,
                person_resolution=_slow_service(persistence),
            ).resolve_extraction_run(run_id)

        return run

    assert _race([job(run_id) for run_id in runs]) == []


def test_concurrent_reordered_mentions_do_not_create_duplicate_persons(
    session_factory: sessionmaker[Session],
) -> None:
    _concurrent_extraction_runs(session_factory, "Иван Иванович Иванов", "Иванов Иван Иванович")

    assert _count(session_factory, PersonRecord) == 1
    with session_factory() as session:
        linked = session.scalars(select(EntityMentionRecord.person_id)).all()
    # The second worker saw the first person and linked the reordered name to it.
    assert len(set(linked)) == 1 and None not in linked


def test_concurrent_same_name_without_evidence_creates_one_person(
    session_factory: sessionmaker[Session],
) -> None:
    # No unique index any more: the identity-block lock alone prevents the duplicate.
    _concurrent_extraction_runs(
        session_factory, "Алексей Сергеевич Иванов", "Алексей Сергеевич Иванов"
    )

    assert _count(session_factory, PersonRecord) == 1


def test_direct_resolve_mention_takes_the_identity_lock_before_deciding(
    session_factory: sessionmaker[Session],
) -> None:
    _, mentions = seed_mentions(session_factory, "Иван Иванов", "Иван Иванов")
    service = _slow_service(SqlAlchemyPersonPersistence(session_factory))

    def job(mention_id: int) -> Callable[[], None]:
        def run() -> None:
            with session_factory.begin() as session:
                service.resolve_mention(session, session.get_one(EntityMentionRecord, mention_id))

        return run

    assert _race([job(mention_id) for mention_id in mentions]) == []
    assert _count(session_factory, PersonRecord) == 1
    with session_factory() as session:
        linked = session.scalars(select(EntityMentionRecord.person_id)).all()
    assert len(set(linked)) == 1 and None not in linked


def test_namesakes_stay_visible_with_the_smallest_candidate_limit(
    session_factory: sessionmaker[Session],
) -> None:
    # A lower-id person whose alias has the same key must not push a namesake out.
    seed_person(session_factory, "Пётр Петров", aliases=("Алексей Сергеевич Иванов",))
    for _ in range(2):
        seed_person(session_factory, "Алексей Сергеевич Иванов")
    service = _service(session_factory, {"ER_CANDIDATE_LIMIT": "2"})

    mention_id, action, linked = _resolve(session_factory, "Алексей Сергеевич Иванов", service)

    assert (action, linked) == (A.REVIEW, None)
    decision = _decision(session_factory, mention_id)
    assert len(decision.candidates) == 2
    assert "multiple_exact_name_matches" in decision.reasons
