"""Evaluate a ResearchResponse after the database search. No LLM involved.

Combines the two deterministic decisions made after research: per-person
review (ResearchReviewPolicy) and source routing (ResearchPlanner.route).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from research.models import ResearchRequest, ResearchResponse
from research.planning.models import ResearchPlan, SourceRoutingDecision
from research.planning.planner import ResearchPlanner
from research.reports.models import ResearchReviewDecision
from research.reports.review_policy import ResearchReviewPolicy


class PersonReviewDecision(BaseModel):
    person_id: int
    decision: ResearchReviewDecision


class ResearchResultEvaluation(BaseModel):
    routing: SourceRoutingDecision
    reviews: list[PersonReviewDecision] = Field(default_factory=list)

    def decisions_by_person(self) -> dict[int, ResearchReviewDecision]:
        return {review.person_id: review.decision for review in self.reviews}

    @property
    def review_required(self) -> bool:
        return any(review.decision.required for review in self.reviews)


class ResearchResultEvaluator:
    def __init__(self, *, planner: ResearchPlanner, review_policy: ResearchReviewPolicy) -> None:
        self._planner = planner
        self._review_policy = review_policy

    def evaluate(
        self,
        *,
        request: ResearchRequest,
        plan: ResearchPlan,
        response: ResearchResponse,
    ) -> ResearchResultEvaluation:
        reviews: list[PersonReviewDecision] = []
        for result in response.results:
            if result.person.id is None:  # persisted persons always have an id
                raise ValueError("research result person has no id")
            reviews.append(
                PersonReviewDecision(
                    person_id=result.person.id,
                    decision=self._review_policy.evaluate(request=request, result=result),
                )
            )
        return ResearchResultEvaluation(
            routing=self._planner.route(plan, response),
            reviews=reviews,
        )
