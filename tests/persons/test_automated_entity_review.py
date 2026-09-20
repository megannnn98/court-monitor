"""`AutomatedEntityReviewService` on PostgreSQL: audit, applied actions, idempotency.

The reviewer is a scripted in-memory implementation of the protocol — no network, no API
key — and everything else is the production code path: the real decision records, the
real review service, the real policy.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from support.research_db_fixtures import ResearchSeeder

from db.orm_models import (
    EntityMentionRecord,
    PersonRecord,
    PersonResolutionAiReviewRecord,
    PersonResolutionDecisionRecord,
)
from persons.persistence import SqlAlchemyPersonPersistence
from persons.resolution.ai_policy import (
    EntityReviewOutcome,
    EntityReviewPolicy,
    EntityReviewProvider,
    EntityReviewSettings,
)
from persons.resolution.ai_review import (
    EntityReviewDecision,
    EntityReviewError,
    EntityReviewRequest,
    EntityReviewResult,
)
from persons.resolution.ai_review_service import AutomatedEntityReviewService
from persons.resolution.review import (
    PersonResolutionReviewService,
    ResolutionReviewAction,
    ResolutionReviewResult,
    ResolutionReviewStateError,
)
from persons.resolution.service import DecisionStatus

MODEL = "stub/model"
SETTINGS = EntityReviewSettings(
    provider=EntityReviewProvider.TOGETHER, model=MODEL, max_retries=2, prompt_version="v1"
)


class ScriptedReviewer:
    """Answers with `answers` in order; an `Exception` in the list is raised instead."""

    def __init__(self, answers: Sequence[EntityReviewResult | Exception]) -> None:
        self._answers = list(answers)
        self.requests: list[EntityReviewRequest] = []

    def review(self, request: EntityReviewRequest) -> EntityReviewResult:
        self.requests.append(request)
        answer = self._answers[min(len(self.requests) - 1, len(self._answers) - 1)]
        if isinstance(answer, Exception):
            raise answer
        return answer


def answer(
    decision: EntityReviewDecision, confidence: float, explanation: str = "because"
) -> EntityReviewResult:
    return EntityReviewResult(
        decision=decision,
        confidence=confidence,
        supporting_evidence=["the same case"],
        explanation=explanation,
    )


def _pending_decision(
    session: Session,
    *,
    mention_name: str = "Иван Иванов",
    candidate_names: Sequence[str] = ("Иван Иванов",),
    reasons: Sequence[str] = ("medium_confidence_match", "name_only_evidence"),
    conflicts: Sequence[str] = (),
    scores: Sequence[float] = (0.8,),
) -> tuple[int, list[int], int]:
    """A decision as ER v2 stores it for review: one mention, its candidate persons."""
    seed = ResearchSeeder(session)
    source_id = seed.source("ovd-info", "https://ovdinfo.example.test")
    text = f"Суд арестовал {mention_name} по делу о пикете."
    _, run_id = seed.article(source_id, external_id="news-1", title="Задержание", text=text)
    mention_id = seed.mention(run_id, mention_name, person_id=None)
    person_ids = []
    for index, name in enumerate(candidate_names):
        person_id = seed.person(name)
        other_text = f"Ранее {name} выходил в пикет, дело № 1-{index}/2026."
        _, other_run = seed.article(
            source_id, external_id=f"old-{index}", title="Пикет", text=other_text
        )
        seed.mention(other_run, name, person_id=person_id)
        person_ids.append(person_id)
    decision = PersonResolutionDecisionRecord(
        mention_id=mention_id,
        resolver_version="er-v2",
        method="full",
        action="review",
        status=DecisionStatus.PENDING_REVIEW.value,
        reasons=list(reasons),
        identity={
            "name": mention_name,
            "surface_text": mention_name,
            "matching_key": mention_name.lower().replace(" ", "|"),
            "article_id": None,
            "normalized": {"canonical_form": mention_name.lower()},
        },
        candidates=[
            {
                "candidate": {
                    "person_id": person_id,
                    "canonical_name": name,
                    "matching_key": name.lower().replace(" ", "|"),
                    "aliases": [],
                    "sources": ["trigram"],
                    "trigram_similarity": 0.7,
                    "semantic_similarity": None,
                },
                "features": {
                    "compared_form": name,
                    "compared_form_is_alias": False,
                    "exact_matching_key": True,
                    "exact_name": True,
                    "exact_alias": False,
                    "surname": "exact",
                    "given_name": "exact",
                    "patronymic": "missing",
                    "surname_similarity": 1.0,
                    "given_name_similarity": 1.0,
                    "patronymic_similarity": None,
                    "full_name_similarity": 1.0,
                    "order_differs": False,
                    "initials_only": False,
                    "incomplete_name": False,
                    "conflicts": list(conflicts),
                    "same_article_mention": False,
                    "case_context_match": False,
                },
                "score": {"resolution_score": scores[index], "rules": ["exact_name"]},
            }
            for index, (person_id, name) in enumerate(zip(person_ids, candidate_names, strict=True))
        ],
        semantic_source="disabled",
    )
    session.add(decision)
    session.flush()
    return decision.id, person_ids, mention_id


def _service(
    session_factory: sessionmaker[Session],
    reviewer: ScriptedReviewer,
    *,
    settings: EntityReviewSettings = SETTINGS,
    review_service: PersonResolutionReviewService | None = None,
) -> AutomatedEntityReviewService:
    return AutomatedEntityReviewService(
        session_factory,
        reviewer,
        settings=settings,
        model=settings.model or MODEL,
        policy=EntityReviewPolicy(auto_threshold=settings.auto_threshold),
        review_service=review_service,
        sleep=lambda _seconds: None,
    )


def _audit(session: Session, decision_id: int) -> list[PersonResolutionAiReviewRecord]:
    return list(
        session.scalars(
            select(PersonResolutionAiReviewRecord)
            .where(PersonResolutionAiReviewRecord.decision_id == decision_id)
            .order_by(PersonResolutionAiReviewRecord.id)
        ).all()
    )


def test_a_confident_same_person_is_linked_and_audited(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        decision_id, person_ids, mention_id = _pending_decision(session)
        session.commit()
    reviewer = ScriptedReviewer([answer(EntityReviewDecision.SAME_PERSON, 0.95)])

    result = _service(session_factory, reviewer).review_pending(limit=10)

    assert (result.reviewed, result.auto_accepted) == (1, 1)
    with session_factory() as session:
        decision = session.get_one(PersonResolutionDecisionRecord, decision_id)
        assert decision.status == DecisionStatus.REVIEWED.value
        assert decision.review_action == ResolutionReviewAction.LINK_TO_PERSON.value
        assert decision.selected_person_id == person_ids[0]
        assert session.get_one(EntityMentionRecord, mention_id).person_id == person_ids[0]
        rows = _audit(session, decision_id)
        assert len(rows) == 1
        row = rows[0]
        assert row.outcome == EntityReviewOutcome.AUTO_ACCEPTED.value
        assert row.applied_action == ResolutionReviewAction.LINK_TO_PERSON.value
        assert row.applied_person_id == person_ids[0]
        assert row.decision == EntityReviewDecision.SAME_PERSON.value
        assert row.confidence == 0.95
        assert row.provider == "together"
        assert (row.model, row.prompt_version) == (MODEL, "v1")
        assert len(row.input_hash) == 64
        assert row.candidate_reviews[0]["person_id"] == person_ids[0]
        assert row.candidate_reviews[0]["result"]["decision"] == "same_person"
        assert row.provider_calls == 1
        assert row.duration_ms is not None


def test_a_confident_different_person_creates_a_person_instead_of_linking(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        decision_id, person_ids, mention_id = _pending_decision(session)
        session.commit()
    reviewer = ScriptedReviewer([answer(EntityReviewDecision.DIFFERENT_PERSON, 0.93)])

    result = _service(session_factory, reviewer).review_pending(limit=10)

    assert (result.reviewed, result.auto_rejected) == (1, 1)
    with session_factory() as session:
        decision = session.get_one(PersonResolutionDecisionRecord, decision_id)
        assert decision.review_action == ResolutionReviewAction.CREATE_NEW_PERSON.value
        linked = session.get_one(EntityMentionRecord, mention_id).person_id
        assert linked is not None and linked not in person_ids
        assert session.get_one(PersonRecord, linked).canonical_name == "Иван Иванов"
        assert _audit(session, decision_id)[0].outcome == EntityReviewOutcome.AUTO_REJECTED.value


def test_an_uncertain_review_leaves_the_decision_to_a_human(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        decision_id, _, mention_id = _pending_decision(session)
        session.commit()
    reviewer = ScriptedReviewer([answer(EntityReviewDecision.UNCERTAIN, 0.4)])

    result = _service(session_factory, reviewer).review_pending(limit=10)

    assert (result.reviewed, result.human_required) == (1, 1)
    with session_factory() as session:
        decision = session.get_one(PersonResolutionDecisionRecord, decision_id)
        assert decision.status == DecisionStatus.PENDING_REVIEW.value
        assert decision.review_action is None
        assert session.get_one(EntityMentionRecord, mention_id).person_id is None
        row = _audit(session, decision_id)[0]
        assert row.outcome == EntityReviewOutcome.HUMAN_REQUIRED.value
        assert row.applied_action is None
        assert row.resolution_reason == "uncertain"


def test_conflicting_identity_data_is_never_linked_automatically(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        decision_id, _, mention_id = _pending_decision(
            session,
            conflicts=("patronymic_mismatch",),
            reasons=("conflicting_identity_data", "medium_confidence_match"),
        )
        session.commit()
    reviewer = ScriptedReviewer([answer(EntityReviewDecision.SAME_PERSON, 0.99)])

    result = _service(session_factory, reviewer).review_pending(limit=10)

    assert result.human_required == 1
    with session_factory() as session:
        assert session.get_one(EntityMentionRecord, mention_id).person_id is None
        assert "patronymic_mismatch" in _audit(session, decision_id)[0].resolution_reason


def test_a_provider_failure_keeps_the_decision_for_a_human(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        decision_id, _, mention_id = _pending_decision(session)
        session.commit()
    reviewer = ScriptedReviewer([EntityReviewError("timed out", transient=True)])

    result = _service(session_factory, reviewer).review_pending(limit=10)

    assert (result.reviewed, result.failed) == (1, 1)
    # Three attempts: the first and two retries (max_retries=2).
    assert len(reviewer.requests) == 3
    with session_factory() as session:
        decision = session.get_one(PersonResolutionDecisionRecord, decision_id)
        assert decision.status == DecisionStatus.PENDING_REVIEW.value
        assert session.get_one(EntityMentionRecord, mention_id).person_id is None
        row = _audit(session, decision_id)[0]
        assert row.outcome == EntityReviewOutcome.FAILED.value
        assert "timed out" in row.resolution_reason
        assert row.applied_action is None


def test_a_transient_failure_is_retried_and_then_succeeds(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        decision_id, person_ids, _ = _pending_decision(session)
        session.commit()
    reviewer = ScriptedReviewer(
        [
            EntityReviewError("Together AI is unreachable", transient=True),
            answer(EntityReviewDecision.SAME_PERSON, 0.95),
        ]
    )

    result = _service(session_factory, reviewer).review_pending(limit=10)

    assert result.auto_accepted == 1
    assert len(reviewer.requests) == 2
    with session_factory() as session:
        row = _audit(session, decision_id)[0]
        assert row.provider_calls == 2
        assert row.applied_person_id == person_ids[0]


def test_a_broken_contract_is_not_retried(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        _pending_decision(session)
        session.commit()
    reviewer = ScriptedReviewer([EntityReviewError("does not match the contract")])

    result = _service(session_factory, reviewer).review_pending(limit=10)

    assert result.failed == 1
    assert len(reviewer.requests) == 1


def test_the_same_input_is_reviewed_once(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        decision_id, _, _ = _pending_decision(session)
        session.commit()
    reviewer = ScriptedReviewer([answer(EntityReviewDecision.UNCERTAIN, 0.3)])
    service = _service(session_factory, reviewer)

    first = service.review_pending(limit=10)
    second = service.review_pending(limit=10)

    assert (first.reviewed, first.human_required) == (1, 1)
    assert (second.reviewed, second.skipped) == (0, 1)
    assert len(reviewer.requests) == 1
    with session_factory() as session:
        assert len(_audit(session, decision_id)) == 1


def test_a_new_prompt_version_reviews_again_and_keeps_the_history(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        decision_id, person_ids, _ = _pending_decision(session)
        session.commit()
    first = ScriptedReviewer([answer(EntityReviewDecision.UNCERTAIN, 0.3)])
    _service(session_factory, first).review_pending(limit=10)

    second = ScriptedReviewer([answer(EntityReviewDecision.SAME_PERSON, 0.96)])
    result = _service(
        session_factory,
        second,
        settings=EntityReviewSettings(
            provider=EntityReviewProvider.TOGETHER, model=MODEL, prompt_version="v2"
        ),
    ).review_pending(limit=10)

    assert result.auto_accepted == 1
    with session_factory() as session:
        rows = _audit(session, decision_id)
        assert [row.prompt_version for row in rows] == ["v1", "v2"]
        assert [row.outcome for row in rows] == ["human_required", "auto_accepted"]
        assert rows[1].applied_person_id == person_ids[0]


def test_a_decision_a_human_already_resolved_is_not_reviewed(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        decision_id, person_ids, _ = _pending_decision(session)
        decision = session.get_one(PersonResolutionDecisionRecord, decision_id)
        decision.status = DecisionStatus.REVIEWED.value
        decision.selected_person_id = person_ids[0]
        session.commit()
    reviewer = ScriptedReviewer([answer(EntityReviewDecision.SAME_PERSON, 0.99)])

    result = _service(session_factory, reviewer).review_pending(limit=10)

    assert (result.reviewed, result.skipped) == (0, 0)
    assert reviewer.requests == []
    with session_factory() as session:
        assert _audit(session, decision_id) == []


def test_a_failed_action_rolls_back_the_audit_and_stays_with_a_human(
    session_factory: sessionmaker[Session],
) -> None:
    """The action cannot be applied (the state changed under the review): nothing is
    linked, and the failure — not the accepted decision — is what the audit keeps."""

    class RefusingReviewService(PersonResolutionReviewService):
        def apply(self, *args: object, **kwargs: object) -> ResolutionReviewResult:
            raise ResolutionReviewStateError("decision 1 is reviewed, not pending review")

    with session_factory() as session:
        decision_id, _person_ids, mention_id = _pending_decision(session)
        session.commit()
    reviewer = ScriptedReviewer([answer(EntityReviewDecision.SAME_PERSON, 0.97)])

    result = _service(
        session_factory,
        reviewer,
        review_service=RefusingReviewService(SqlAlchemyPersonPersistence(session_factory)),
    ).review_pending(limit=10)

    assert (result.reviewed, result.failed) == (1, 1)
    with session_factory() as session:
        decision = session.get_one(PersonResolutionDecisionRecord, decision_id)
        assert decision.status == DecisionStatus.PENDING_REVIEW.value
        assert decision.review_action is None
        assert session.get_one(EntityMentionRecord, mention_id).person_id is None
        rows = _audit(session, decision_id)
        assert len(rows) == 1
        assert rows[0].outcome == EntityReviewOutcome.FAILED.value
        assert "ResolutionReviewStateError" in rows[0].resolution_reason
        assert rows[0].applied_person_id is None


def test_a_candidate_that_is_no_longer_active_is_not_reviewed(
    session_factory: sessionmaker[Session],
) -> None:
    """ER compared a person that was merged away since: there is nothing to review, and
    the decision waits for a human instead of linking to a dead person."""
    with session_factory() as session:
        decision_id, person_ids, mention_id = _pending_decision(session)
        session.get_one(PersonRecord, person_ids[0]).status = "merged"
        session.commit()
    reviewer = ScriptedReviewer([answer(EntityReviewDecision.SAME_PERSON, 0.97)])

    result = _service(session_factory, reviewer).review_pending(limit=10)

    assert (result.reviewed, result.human_required) == (1, 1)
    assert reviewer.requests == []
    with session_factory() as session:
        assert session.get_one(EntityMentionRecord, mention_id).person_id is None
        row = _audit(session, decision_id)[0]
        assert row.resolution_reason == "no_candidate_to_review"
        assert row.decision is None


def test_one_failing_decision_does_not_stop_the_batch(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        first_id, _, _ = _pending_decision(session)
        session.commit()
    with session_factory() as session:
        seed = ResearchSeeder(session)
        source_id = seed.source("sota", "https://sota.example.test")
        text = "Суд арестовал Петра Петрова."
        _, run_id = seed.article(source_id, external_id="news-2", title="Суд", text=text)
        mention_id = seed.mention(run_id, "Петра Петрова", person_id=None)
        person_id = seed.person("Пётр Петров")
        second = PersonResolutionDecisionRecord(
            mention_id=mention_id,
            resolver_version="er-v2",
            method="full",
            action="review",
            status=DecisionStatus.PENDING_REVIEW.value,
            reasons=["medium_confidence_match"],
            identity={
                "name": "Пётр Петров",
                "surface_text": "Петра Петрова",
                "matching_key": "петров|пётр",
                "article_id": None,
                "normalized": {"canonical_form": "пётр петров"},
            },
            candidates=[
                {
                    "candidate": {
                        "person_id": person_id,
                        "canonical_name": "Пётр Петров",
                        "matching_key": "петров|пётр",
                        "aliases": [],
                        "sources": ["exact_key"],
                        "trigram_similarity": None,
                        "semantic_similarity": None,
                    },
                    "features": {
                        "compared_form": "Пётр Петров",
                        "compared_form_is_alias": False,
                        "exact_matching_key": True,
                        "exact_name": True,
                        "exact_alias": False,
                        "surname": "exact",
                        "given_name": "exact",
                        "patronymic": "missing",
                        "surname_similarity": 1.0,
                        "given_name_similarity": 1.0,
                        "patronymic_similarity": None,
                        "full_name_similarity": 1.0,
                        "order_differs": False,
                        "initials_only": False,
                        "incomplete_name": False,
                        "conflicts": [],
                        "same_article_mention": False,
                        "case_context_match": False,
                    },
                    "score": {"resolution_score": 0.82, "rules": ["exact_name"]},
                }
            ],
            semantic_source="disabled",
        )
        session.add(second)
        session.flush()
        second_id = second.id
        session.commit()

    class FailingThenWorking:
        def __init__(self) -> None:
            self.seen: list[int] = []

        def review(self, request: EntityReviewRequest) -> EntityReviewResult:
            self.seen.append(request.decision_id)
            if request.decision_id == first_id:
                raise EntityReviewError("broken answer")
            return answer(EntityReviewDecision.SAME_PERSON, 0.96)

    reviewer = FailingThenWorking()
    service = AutomatedEntityReviewService(
        session_factory,
        reviewer,
        settings=SETTINGS,
        model=MODEL,
        sleep=lambda _seconds: None,
    )

    result = service.review_pending(limit=10)

    assert (result.reviewed, result.failed, result.auto_accepted) == (2, 1, 1)
    assert reviewer.seen == [first_id, second_id]
    with session_factory() as session:
        assert (
            session.get_one(PersonResolutionDecisionRecord, second_id).status
            == DecisionStatus.REVIEWED.value
        )


def test_several_candidates_are_all_reviewed_before_the_policy_decides(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        decision_id, person_ids, mention_id = _pending_decision(
            session,
            candidate_names=("Иван Иванов", "Иван Иванов"),
            reasons=("multiple_plausible_candidates", "medium_confidence_match"),
            scores=(0.84, 0.8),
        )
        session.commit()
    reviewer = ScriptedReviewer(
        [
            answer(EntityReviewDecision.DIFFERENT_PERSON, 0.95),
            answer(EntityReviewDecision.SAME_PERSON, 0.94),
        ]
    )

    result = _service(session_factory, reviewer).review_pending(limit=10)

    assert result.auto_accepted == 1
    assert [request.candidate.person_id for request in reviewer.requests] == person_ids
    with session_factory() as session:
        assert session.get_one(EntityMentionRecord, mention_id).person_id == person_ids[1]
        row = _audit(session, decision_id)[0]
        assert len(row.candidate_reviews) == 2
        assert row.applied_person_id == person_ids[1]


def test_the_review_request_carries_the_quote_of_the_mention_and_of_the_candidate(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        _pending_decision(session)
        session.commit()
    reviewer = ScriptedReviewer([answer(EntityReviewDecision.UNCERTAIN, 0.3)])

    _service(session_factory, reviewer).review_pending(limit=10)

    request = reviewer.requests[0]
    assert "Суд арестовал Иван Иванов" in request.mention.evidence[0].text
    assert request.mention.evidence[0].source_name == "ovd-info"
    assert any("дело № 1-0/2026" in item.text for item in request.candidate.evidence)
    assert request.matched_features[:2] == ("surname:exact", "given_name:exact")
    assert request.deterministic_score == 0.8


@pytest.mark.parametrize("confidence", [0.9, 0.8999])
def test_the_configured_threshold_decides_the_boundary(
    session_factory: sessionmaker[Session], confidence: float
) -> None:
    with session_factory() as session:
        _pending_decision(session)
        session.commit()
    reviewer = ScriptedReviewer([answer(EntityReviewDecision.SAME_PERSON, confidence)])

    result = _service(session_factory, reviewer).review_pending(limit=10)

    if confidence >= 0.9:
        assert result.auto_accepted == 1
    else:
        assert result.human_required == 1


def test_a_transient_failure_can_be_retried_on_the_next_run(
    session_factory: sessionmaker[Session],
) -> None:
    """A provider outage must not retire a decision from AI review for good."""
    with session_factory() as session:
        decision_id, person_ids, _ = _pending_decision(session)
        session.commit()
    failing = ScriptedReviewer([EntityReviewError("timed out", transient=True)])
    _service(session_factory, failing).review_pending(limit=10)

    working = ScriptedReviewer([answer(EntityReviewDecision.SAME_PERSON, 0.95)])
    result = _service(session_factory, working).review_pending(limit=10)

    assert (result.reviewed, result.auto_accepted, result.skipped) == (1, 1, 0)
    with session_factory() as session:
        rows = _audit(session, decision_id)
        assert [row.outcome for row in rows] == ["failed", "auto_accepted"]
        assert rows[1].applied_person_id == person_ids[0]


def test_provider_calls_count_every_call_the_review_cost(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        decision_id, _, _ = _pending_decision(
            session, candidate_names=("Иван Иванов", "Иван Иванов"), scores=(0.84, 0.8)
        )
        session.commit()
    reviewer = ScriptedReviewer([answer(EntityReviewDecision.UNCERTAIN, 0.3)])

    _service(session_factory, reviewer).review_pending(limit=10)

    with session_factory() as session:
        # Two candidates, one call each: not two "attempts" of one review.
        assert _audit(session, decision_id)[0].provider_calls == 2


def test_a_failed_review_records_every_attempt_it_made(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        decision_id, _, _ = _pending_decision(session)
        session.commit()
    reviewer = ScriptedReviewer([EntityReviewError("unreachable", transient=True)])

    _service(session_factory, reviewer).review_pending(limit=10)

    with session_factory() as session:
        row = _audit(session, decision_id)[0]
        # The first call plus max_retries=2 retries.
        assert row.provider_calls == 3
        assert len(reviewer.requests) == 3


def test_a_human_resolving_the_decision_during_the_review_wins(
    session_factory: sessionmaker[Session],
) -> None:
    """The model answers with no lock held, so the decision is re-checked before the
    action is applied: a human who resolved it meanwhile is not overwritten."""
    with session_factory() as session:
        decision_id, _person_ids, mention_id = _pending_decision(session)
        session.commit()

    class ReviewerThatLetsAHumanIn:
        def __init__(self) -> None:
            self.requests: list[EntityReviewRequest] = []

        def review(self, request: EntityReviewRequest) -> EntityReviewResult:
            self.requests.append(request)
            # A second connection can read and write the decision while the model works.
            with session_factory() as other:
                decision = other.get_one(PersonResolutionDecisionRecord, request.decision_id)
                decision.status = DecisionStatus.REVIEWED.value
                decision.review_action = ResolutionReviewAction.CREATE_NEW_PERSON.value
                other.commit()
            return answer(EntityReviewDecision.SAME_PERSON, 0.99)

    reviewer = ReviewerThatLetsAHumanIn()
    service = AutomatedEntityReviewService(
        session_factory, reviewer, settings=SETTINGS, model=MODEL, sleep=lambda _seconds: None
    )

    result = service.review_pending(limit=10)

    assert (result.reviewed, result.skipped) == (0, 1)
    with session_factory() as session:
        decision = session.get_one(PersonResolutionDecisionRecord, decision_id)
        assert decision.review_action == ResolutionReviewAction.CREATE_NEW_PERSON.value
        assert decision.selected_person_id is None
        assert session.get_one(EntityMentionRecord, mention_id).person_id is None
        assert _audit(session, decision_id) == []


def test_a_person_deactivated_during_the_review_is_not_linked(
    session_factory: sessionmaker[Session],
) -> None:
    """The candidate was active when the context was built and is merged away by the time
    the action is applied: the link fails and the decision stays with a human."""
    with session_factory() as session:
        decision_id, person_ids, mention_id = _pending_decision(session)
        session.commit()

    class ReviewerThatDeactivatesTheCandidate:
        def __init__(self) -> None:
            self.requests: list[EntityReviewRequest] = []

        def review(self, request: EntityReviewRequest) -> EntityReviewResult:
            self.requests.append(request)
            with session_factory() as other:
                other.get_one(PersonRecord, person_ids[0]).status = "merged"
                other.commit()
            return answer(EntityReviewDecision.SAME_PERSON, 0.99)

    service = AutomatedEntityReviewService(
        session_factory,
        ReviewerThatDeactivatesTheCandidate(),
        settings=SETTINGS,
        model=MODEL,
        sleep=lambda _seconds: None,
    )

    result = service.review_pending(limit=10)

    assert (result.reviewed, result.failed) == (1, 1)
    with session_factory() as session:
        decision = session.get_one(PersonResolutionDecisionRecord, decision_id)
        assert decision.status == DecisionStatus.PENDING_REVIEW.value
        assert session.get_one(EntityMentionRecord, mention_id).person_id is None
        rows = _audit(session, decision_id)
        assert len(rows) == 1
        assert rows[0].outcome == EntityReviewOutcome.FAILED.value
        assert "ResolutionReviewStateError" in rows[0].resolution_reason
