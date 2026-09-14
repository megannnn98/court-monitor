"""The single place that decides whether a research result needs human review.

Graph nodes, the report builder and adapters ask this policy; none of them
carries its own review conditions.
"""

from __future__ import annotations

from candidates.models import DEFAULT_MIN_PERSECUTION_CONFIDENCE, RosfinmonitoringStatus
from persecution.models import PersecutionClassification, PersecutionClassificationStatus
from research.models import PersonResearchResult, ResearchRequest, ResearchRosfinmonitoring
from research.reports.citations import citations, event_citations
from research.reports.models import (
    ResearchReviewDecision,
    ResearchReviewReason,
    ResearchReviewReasonDetail,
    ResearchReviewSeverity,
)

_PERSECUTION_REASONS: dict[PersecutionClassificationStatus, tuple[ResearchReviewReason, str]] = {
    PersecutionClassificationStatus.UNCERTAIN: (
        ResearchReviewReason.PERSECUTION_UNCERTAIN,
        "Классификация преследования неопределённа: политический характер не установлен.",
    ),
    PersecutionClassificationStatus.NEEDS_REVIEW: (
        ResearchReviewReason.PERSECUTION_NEEDS_REVIEW,
        "Классификация преследования помечена как требующая проверки.",
    ),
}

_ROSFIN_REASONS: dict[RosfinmonitoringStatus, tuple[ResearchReviewReason, str]] = {
    RosfinmonitoringStatus.AMBIGUOUS: (
        ResearchReviewReason.ROSFIN_AMBIGUOUS,
        (
            "Человеку соответствуют несколько записей перечня: ни присутствие, ни отсутствие "
            "не подтверждены."
        ),
    ),
    RosfinmonitoringStatus.NEEDS_REVIEW: (
        ResearchReviewReason.ROSFIN_NEEDS_REVIEW,
        "Сопоставление с перечнем требует проверки человеком.",
    ),
    RosfinmonitoringStatus.INSUFFICIENT_DATA: (
        ResearchReviewReason.ROSFIN_INSUFFICIENT_DATA,
        "Недостаточно данных для надёжного сопоставления с перечнем; отсутствие не подтверждено.",
    ),
}


class ResearchReviewPolicy:
    def evaluate(
        self,
        *,
        request: ResearchRequest,
        result: PersonResearchResult,
    ) -> ResearchReviewDecision:
        # `request` is part of the contract so criterion-dependent rules stay
        # here; the current rules depend on the result only.
        del request
        reasons: list[ResearchReviewReasonDetail] = []
        if result.persecution is not None:
            reasons.extend(self.persecution_reasons(result.persecution))
        if result.rosfinmonitoring is not None:
            reasons.extend(self.rosfinmonitoring_reasons(result.rosfinmonitoring))
        reasons.extend(self.evidence_reasons(result))
        return ResearchReviewDecision(reasons=reasons)

    def persecution_reasons(
        self, persecution: PersecutionClassification
    ) -> list[ResearchReviewReasonDetail]:
        reasons: list[ResearchReviewReasonDetail] = []
        if persecution.status in _PERSECUTION_REASONS:
            code, message = _PERSECUTION_REASONS[persecution.status]
            reasons.append(
                ResearchReviewReasonDetail(
                    code=code,
                    severity=ResearchReviewSeverity.BLOCKING,
                    message=message,
                    classification_id=persecution.id,
                )
            )
        if (
            persecution.status is PersecutionClassificationStatus.POLITICAL
            and persecution.confidence < DEFAULT_MIN_PERSECUTION_CONFIDENCE
        ):
            reasons.append(
                ResearchReviewReasonDetail(
                    code=ResearchReviewReason.LOW_CONFIDENCE,
                    severity=ResearchReviewSeverity.ADVISORY,
                    message=(
                        f"Уверенность классификации {persecution.confidence:.2f} ниже "
                        f"продуктового порога {DEFAULT_MIN_PERSECUTION_CONFIDENCE:.2f}."
                    ),
                    classification_id=persecution.id,
                )
            )
        return reasons

    def rosfinmonitoring_reasons(
        self, rosfinmonitoring: ResearchRosfinmonitoring
    ) -> list[ResearchReviewReasonDetail]:
        if rosfinmonitoring.status not in _ROSFIN_REASONS:
            return []
        code, message = _ROSFIN_REASONS[rosfinmonitoring.status]
        return [
            ResearchReviewReasonDetail(
                code=code,
                severity=ResearchReviewSeverity.BLOCKING,
                message=message,
                snapshot_id=rosfinmonitoring.snapshot_id,
            )
        ]

    def evidence_reasons(self, result: PersonResearchResult) -> list[ResearchReviewReasonDetail]:
        reasons: list[ResearchReviewReasonDetail] = []
        if not citations(result):
            # Identity and classification are derived from articles; without a
            # single citable span neither is a verifiable source fact.
            reasons.append(
                ResearchReviewReasonDetail(
                    code=ResearchReviewReason.MISSING_EVIDENCE,
                    severity=ResearchReviewSeverity.BLOCKING,
                    message=(
                        "У человека нет ни одного фрагмента источника, на который можно сослаться."
                    ),
                    classification_id=None if result.persecution is None else result.persecution.id,
                )
            )
        for event in result.events:
            if not event_citations(result, event.event_id):
                reasons.append(
                    ResearchReviewReasonDetail(
                        code=ResearchReviewReason.MISSING_EVIDENCE,
                        severity=ResearchReviewSeverity.BLOCKING,
                        message=(
                            f"Для события {event.event_type.value} (#{event.event_id}) нет "
                            "фрагмента источника."
                        ),
                        event_id=event.event_id,
                    )
                )
        return reasons
