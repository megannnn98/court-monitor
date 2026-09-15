"""Deterministic ResearchReport from ResearchRequest + ResearchResponse.

The builder copies statuses and confidences, words them with fixed text and
attaches citations from existing evidence. It never re-derives a status,
never turns an unresolved status into a negative, and uses no LLM.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

from candidates.models import RosfinmonitoringStatus
from persecution.models import PersecutionClassificationStatus
from research.models import (
    PersonResearchCriteria,
    PersonResearchResult,
    ResearchEvent,
    ResearchRequest,
    ResearchResponse,
    ResearchRosfinmonitoring,
)
from research.planning.models import (
    ResearchPlan,
    ResearchRetrievalMode,
    SourceRoutingDecision,
    SourceRoutingReason,
)
from research.reports.citations import (
    citations,
    event_citations,
    evidence_without_source,
    mention_citations,
)
from research.reports.evaluation import ResearchResultEvaluation
from research.reports.models import (
    ResearchClaim,
    ResearchClaimBasis,
    ResearchClaimType,
    ResearchMatchReason,
    ResearchReport,
    ResearchReportItem,
    ResearchReportStatus,
    ResearchReportSummary,
    ResearchReportWarning,
    ResearchReportWarningCode,
    ResearchRetrievalMetadata,
    ResearchReviewDecision,
    ResearchReviewReason,
)
from semantic_retrieval.relevance import SemanticRetrievalDecision

_PERSECUTION_TEXT: dict[PersecutionClassificationStatus, str] = {
    PersecutionClassificationStatus.POLITICAL: "Преследование классифицировано как политическое",
    # The rule-based classifier says NON_POLITICAL when it finds no political signal:
    # that is an absence of evidence, never an established non-political fact.
    PersecutionClassificationStatus.NON_POLITICAL: (
        "Признаков политического преследования в источниках не найдено; это не подтверждает, "
        "что преследование неполитическое"
    ),
    PersecutionClassificationStatus.UNCERTAIN: (
        "Классификация неопределённа: политический характер преследования не установлен"
    ),
    PersecutionClassificationStatus.NEEDS_REVIEW: (
        "Классификация требует проверки человеком: политический характер преследования "
        "не установлен"
    ),
}

# Only NOT_MATCHED is worded as absence; every other status says explicitly
# that absence is not confirmed.
_ROSFIN_TEXT: dict[RosfinmonitoringStatus, str] = {
    RosfinmonitoringStatus.NOT_MATCHED: (
        "Не найден в перечне Росфинмониторинга: сопоставление со snapshot #{snapshot} выполнено."
    ),
    RosfinmonitoringStatus.MATCHED: (
        "Найден в перечне Росфинмониторинга (snapshot #{snapshot}){entry}."
    ),
    RosfinmonitoringStatus.AMBIGUOUS: (
        "Несколько возможных записей в перечне (snapshot #{snapshot}): присутствие не "
        "подтверждено, отсутствие в перечне не подтверждено."
    ),
    RosfinmonitoringStatus.NEEDS_REVIEW: (
        "Сопоставление со snapshot #{snapshot} требует проверки человеком: присутствие не "
        "подтверждено, отсутствие в перечне не подтверждено."
    ),
    RosfinmonitoringStatus.INSUFFICIENT_DATA: (
        "Недостаточно данных для надёжного сопоставления со snapshot #{snapshot}; отсутствие "
        "в перечне не подтверждено."
    ),
    RosfinmonitoringStatus.NO_MATCH_RECORD: (
        "Сопоставление со snapshot #{snapshot} ещё не выполнялось; отсутствие в перечне не "
        "подтверждено."
    ),
}

_PERSECUTION_REVIEW_CODES = frozenset(
    {
        ResearchReviewReason.PERSECUTION_UNCERTAIN,
        ResearchReviewReason.PERSECUTION_NEEDS_REVIEW,
        ResearchReviewReason.LOW_CONFIDENCE,
    }
)
_ROSFIN_REVIEW_CODES = frozenset(
    {
        ResearchReviewReason.ROSFIN_AMBIGUOUS,
        ResearchReviewReason.ROSFIN_NEEDS_REVIEW,
        ResearchReviewReason.ROSFIN_INSUFFICIENT_DATA,
    }
)

_ROUTING_TEXT: dict[SourceRoutingReason, str] = {
    SourceRoutingReason.REFRESH_CANNOT_HELP: (
        "запрос ищет конкретный person_id, обновление источников не создаст этот id"
    ),
    SourceRoutingReason.NO_COMPATIBLE_SOURCE: (
        "нет зарегистрированного источника, совместимого с запросом"
    ),
}


def rosfinmonitoring_text(rf: ResearchRosfinmonitoring) -> str:
    entry = f": {rf.matched_entry_name}" if rf.matched_entry_name else ""
    return _ROSFIN_TEXT[rf.status].format(snapshot=rf.snapshot_id, entry=entry)


class ResearchReportBuilder:
    def build(
        self,
        *,
        request: ResearchRequest,
        response: ResearchResponse,
        evaluation: ResearchResultEvaluation,
        plan: ResearchPlan | None = None,
        semantic: SemanticRetrievalDecision | None = None,
    ) -> ResearchReport:
        decisions = evaluation.decisions_by_person()
        ranks = (
            {} if semantic is None else {hit.entity_id: hit.rank for hit in semantic.accepted.hits}
        )
        metadata = ResearchRetrievalMetadata(
            mode=ResearchRetrievalMode.STRUCTURED if plan is None else plan.retrieval_mode,
            backend=None if semantic is None else semantic.retrieved.backend,
            candidate_pool_size=0 if plan is None else plan.candidate_pool_size,
            candidates_returned=0 if semantic is None else len(semantic.retrieved.hits),
            candidates_accepted=0 if semantic is None else len(semantic.accepted.hits),
            min_similarity=None if semantic is None else semantic.dense_min_score,
        )
        items: list[ResearchReportItem] = []
        for result in response.results:
            assert result.person.id is not None  # checked by the evaluator
            item = self._item(request.criteria, result, decisions[result.person.id])
            rank = ranks.get(result.person.id)
            if rank is not None:
                item.retrieval_rank = rank
                item.why_matched.append(_semantic_match_reason(request, metadata, rank))
            items.append(item)

        warnings: list[ResearchReportWarning] = []
        if semantic is not None:
            warnings.append(
                ResearchReportWarning(
                    code=ResearchReportWarningCode.SEMANTIC_CANDIDATE_POOL,
                    message=(
                        f"Семантический поиск нашёл {metadata.candidates_returned} ближайших "
                        f"кандидатов (максимум {metadata.candidate_pool_size}); достаточно "
                        f"похожими (сходство ≥ {semantic.dense_min_score:.2f}) признаны "
                        f"{metadata.candidates_accepted}, только они проверялись по PostgreSQL. "
                        "Сходство формулировок не является фактом и не повышает уверенность "
                        "ни одного утверждения."
                    ),
                )
            )
        if response.total_matched > len(response.results):
            warnings.append(
                ResearchReportWarning(
                    code=ResearchReportWarningCode.RESULTS_TRUNCATED,
                    message=(
                        f"Показано {len(response.results)} из {response.total_matched} "
                        "подходящих людей (limit запроса)."
                    ),
                )
            )

        status = _report_status(response, items, evaluation.routing)
        return ResearchReport(
            status=status,
            request=request,
            summary=ResearchReportSummary(
                total_matched=response.total_matched,
                returned=len(items),
                review_required_count=sum(item.review_required for item in items),
                partial_count=sum(item.partial for item in items),
                text=_summary_text(status, response, items, evaluation.routing, metadata),
            ),
            items=items,
            warnings=warnings,
            source_routing=evaluation.routing,
            retrieval=metadata,
        )

    def _item(
        self,
        criteria: PersonResearchCriteria,
        result: PersonResearchResult,
        review: ResearchReviewDecision,
    ) -> ResearchReportItem:
        person = result.person
        assert person.id is not None
        codes = {reason.code for reason in review.reasons}
        missing_event_ids = {
            reason.event_id
            for reason in review.reasons
            if reason.code is ResearchReviewReason.MISSING_EVIDENCE and reason.event_id is not None
        }
        person_citations = citations(result)
        warnings: list[ResearchReportWarning] = []

        claims = [
            ResearchClaim(
                claim_type=ResearchClaimType.IDENTITY,
                basis=ResearchClaimBasis.SOURCE_DOCUMENTS,
                text=f"{person.canonical_name} (person #{person.id}) упоминается в источниках.",
                # Mentions first, then event spans that also name the person.
                citations=mention_citations(result)
                + [c for c in person_citations if c.mention_id is None],
                review_required=not person_citations,
            )
        ]

        persecution = result.persecution
        if persecution is None:
            warnings.append(
                ResearchReportWarning(
                    code=ResearchReportWarningCode.PERSECUTION_NOT_CLASSIFIED,
                    message=(
                        "Классификация преследования ещё не выполнялась; это не означает, что "
                        "преследование неполитическое."
                    ),
                )
            )
        else:
            reasons = "; ".join(persecution.reasons)
            claims.append(
                ResearchClaim(
                    claim_type=ResearchClaimType.PERSECUTION_CLASSIFICATION,
                    basis=ResearchClaimBasis.SOURCE_DOCUMENTS,
                    text=_PERSECUTION_TEXT[persecution.status]
                    + (f". Основания: {reasons}." if reasons else "."),
                    confidence=persecution.confidence,
                    # The classifier sees exactly this person's mentions and
                    # events, so its citations are the person's evidence spans.
                    citations=person_citations,
                    review_required=bool(codes & _PERSECUTION_REVIEW_CODES) or not person_citations,
                    classification_id=persecution.id,
                )
            )

        rf = result.rosfinmonitoring
        rf_text: str | None = None
        if rf is not None:
            rf_text = rosfinmonitoring_text(rf)
            claims.append(
                ResearchClaim(
                    claim_type=ResearchClaimType.ROSFINMONITORING_STATUS,
                    basis=ResearchClaimBasis.ROSFINMONITORING_SNAPSHOT,
                    text=rf_text,
                    confidence=rf.confidence,
                    review_required=bool(codes & _ROSFIN_REVIEW_CODES),
                    snapshot_id=rf.snapshot_id,
                )
            )
            if rf.status is RosfinmonitoringStatus.NO_MATCH_RECORD:
                warnings.append(
                    ResearchReportWarning(
                        code=ResearchReportWarningCode.ROSFINMONITORING_NOT_MATCHED_YET,
                        message=(
                            f"Сопоставление со snapshot #{rf.snapshot_id} ещё не выполнялось; "
                            "запустите match-rosfinmonitoring."
                        ),
                    )
                )

        for event in result.events:
            claims.append(
                ResearchClaim(
                    claim_type=ResearchClaimType.EVENT,
                    basis=ResearchClaimBasis.SOURCE_DOCUMENTS,
                    text=f"Событие: {_event_label(event)}.",
                    confidence=event.confidence,
                    citations=event_citations(result, event.event_id),
                    review_required=event.event_id in missing_event_ids,
                    event_id=event.event_id,
                )
            )

        for claim in claims:
            if not claim.supported:
                warnings.append(
                    ResearchReportWarning(
                        code=ResearchReportWarningCode.CLAIM_WITHOUT_CITATION,
                        message=(
                            f"Утверждение «{claim.text}» не подкреплено фрагментом источника и "
                            "не является подтверждённым фактом."
                        ),
                    )
                )
        orphaned = evidence_without_source(result)
        if orphaned:
            warnings.append(
                ResearchReportWarning(
                    code=ResearchReportWarningCode.EVIDENCE_WITHOUT_SOURCE,
                    message=(
                        f"{len(orphaned)} фрагмент(ов) ссылаются на статьи, которых нет в "
                        "источниках результата; они не цитируются."
                    ),
                )
            )

        return ResearchReportItem(
            person_id=person.id,
            canonical_name=person.canonical_name,
            aliases=_aliases(result),
            persecution_status=None if persecution is None else persecution.status,
            persecution_confidence=None if persecution is None else persecution.confidence,
            persecution_reasons=[] if persecution is None else list(persecution.reasons),
            classification_id=None if persecution is None else persecution.id,
            snapshot_id=None if rf is None else rf.snapshot_id,
            rosfinmonitoring_status=None if rf is None else rf.status,
            rosfinmonitoring_confidence=None if rf is None else rf.confidence,
            rosfinmonitoring_summary=rf_text,
            events=list(result.events),
            why_matched=_why_matched(criteria, result),
            claims=claims,
            citations=person_citations,
            domain_warnings=list(result.warnings),
            report_warnings=warnings,
            review=review,
        )


def _aliases(result: PersonResearchResult) -> list[str]:
    aliases: list[str] = []
    for alias in result.aliases:
        if alias.surface_text != result.person.canonical_name and alias.surface_text not in aliases:
            aliases.append(alias.surface_text)
    return aliases


def _event_label(event: ResearchEvent) -> str:
    event_date = event.event_date.date().isoformat() if event.event_date else "дата неизвестна"
    return f"{event.event_type.value} {event_date} (event #{event.event_id})"


def _event_matches(criteria: PersonResearchCriteria, event: ResearchEvent) -> bool:
    """Same rule as the repository: one event satisfies type and date range."""
    if criteria.event_types is not None and event.event_type not in criteria.event_types:
        return False
    if criteria.date_from is None and criteria.date_to is None:
        return True
    if event.event_date is None:
        return False
    if criteria.date_from is not None and event.event_date < datetime.combine(
        criteria.date_from, time.min, UTC
    ):
        return False
    return not (
        criteria.date_to is not None
        and event.event_date
        >= datetime.combine(criteria.date_to + timedelta(days=1), time.min, UTC)
    )


def _why_matched(
    criteria: PersonResearchCriteria, result: PersonResearchResult
) -> list[ResearchMatchReason]:
    """Built only from criteria that were applied, next to this person's data."""
    reasons: list[ResearchMatchReason] = []
    person = result.person

    if criteria.person_id is not None:
        reasons.append(
            ResearchMatchReason(
                criterion="person_id", requested=str(criteria.person_id), actual=str(person.id)
            )
        )

    if criteria.name is not None:
        wanted = criteria.name.casefold()
        names = [person.canonical_name, *(alias.surface_text for alias in result.aliases)]
        matching = next((name for name in names if wanted in name.casefold()), None)
        reasons.append(
            ResearchMatchReason(
                criterion="name",
                requested=criteria.name,
                # The repository also matches normalized forms.
                actual=matching
                or f"{person.canonical_name} (совпадение по нормализованному имени)",
            )
        )

    if criteria.persecution_status is not None:
        threshold = criteria.effective_persecution_min_confidence
        persecution = result.persecution
        reasons.append(
            ResearchMatchReason(
                criterion="persecution_status",
                requested=criteria.persecution_status.value
                + (f" (confidence ≥ {threshold:.2f})" if threshold is not None else ""),
                actual=(
                    f"{persecution.status.value} ({persecution.confidence:.2f})"
                    if persecution is not None
                    else "нет классификации"
                ),
            )
        )

    if criteria.rosfinmonitoring_status is not None:
        rf = result.rosfinmonitoring
        reasons.append(
            ResearchMatchReason(
                criterion="rosfinmonitoring_status",
                requested=f"{criteria.rosfinmonitoring_status.value} (snapshot #{criteria.snapshot_id})",
                actual=rf.status.value if rf is not None else "нет данных",
            )
        )

    if criteria.event_types is not None or criteria.date_from or criteria.date_to:
        requested_parts: list[str] = []
        if criteria.event_types is not None:
            requested_parts.append(", ".join(t.value for t in criteria.event_types))
        if criteria.date_from or criteria.date_to:
            date_from = criteria.date_from.isoformat() if criteria.date_from else "…"
            date_to = criteria.date_to.isoformat() if criteria.date_to else "…"
            requested_parts.append(f"{date_from} – {date_to}")
        matching_events = [e for e in result.events if _event_matches(criteria, e)]
        reasons.append(
            ResearchMatchReason(
                criterion="events",
                requested="; ".join(requested_parts),
                actual="; ".join(_event_label(e) for e in matching_events)
                or "нет подходящих событий в результате",
            )
        )

    if criteria.source is not None:
        count = sum(source.source_name == criteria.source for source in result.sources)
        reasons.append(
            ResearchMatchReason(
                criterion="source",
                requested=criteria.source,
                actual=f"{criteria.source} (статей: {count})",
            )
        )

    return reasons


def _report_status(
    response: ResearchResponse,
    items: list[ResearchReportItem],
    routing: SourceRoutingDecision,
) -> ResearchReportStatus:
    if response.total_matched == 0:
        return (
            ResearchReportStatus.INSUFFICIENT_DATA
            if routing.source_refresh_required
            else ResearchReportStatus.NO_MATCHES
        )
    if any(item.review_required for item in items):
        return ResearchReportStatus.REVIEW_REQUIRED
    if not items or any(item.partial for item in items):
        return ResearchReportStatus.PARTIAL
    return ResearchReportStatus.COMPLETE


def _semantic_match_reason(
    request: ResearchRequest, metadata: ResearchRetrievalMetadata, rank: int
) -> ResearchMatchReason:
    assert request.criteria.semantic_query is not None
    backend = metadata.backend.value if metadata.backend is not None else metadata.mode.value
    return ResearchMatchReason(
        criterion="semantic_query",
        requested=request.criteria.semantic_query,
        actual=(
            f"семантически релевантный кандидат ({backend}), позиция {rank} из "
            f"{metadata.candidates_accepted}; сходство, а не установленный факт"
        ),
    )


def _summary_text(
    status: ResearchReportStatus,
    response: ResearchResponse,
    items: list[ResearchReportItem],
    routing: SourceRoutingDecision,
    retrieval: ResearchRetrievalMetadata,
) -> str:
    structured = retrieval.mode is ResearchRetrievalMode.STRUCTURED
    scope = (
        "В текущей базе"
        if structured
        else f"Среди {retrieval.candidates_accepted} семантически релевантных кандидатов"
    )
    if not structured and retrieval.candidates_accepted == 0:
        # Nearest neighbours were found but none is similar enough: say so, and
        # never claim that such people do not exist.
        refresh = (
            f" Обновление источников ({', '.join(routing.sources)}) — рекомендация, "
            "оно не выполнялось."
            if routing.source_refresh_required
            else ""
        )
        return (
            "В текущем индексе не найдено сущностей с достаточной семантической "
            f"релевантностью запросу (проверено ближайших кандидатов: "
            f"{retrieval.candidates_returned}). Это не доказывает, что таких людей нет: "
            "данные ограничены текущей базой и индексом." + refresh
        )
    if status is ResearchReportStatus.INSUFFICIENT_DATA:
        return (
            f"{scope} найдено 0 подходящих людей. Это не доказывает, что таких людей нет: "
            "локальная копия источников может быть неполной. Обновление источников "
            f"({', '.join(routing.sources)}) — рекомендация, оно не выполнялось."
        )
    if status is ResearchReportStatus.NO_MATCHES:
        return (
            f"{scope} найдено 0 подходящих людей; обновление источников не рекомендуется: "
            f"{_ROUTING_TEXT.get(routing.reason, routing.reason.value)}."
        )
    shown = (
        f"Найдено {response.total_matched}, показано {len(items)}."
        if retrieval.mode is ResearchRetrievalMode.STRUCTURED
        else f"{scope} подходят {response.total_matched}, показано {len(items)}."
    )
    if status is ResearchReportStatus.REVIEW_REQUIRED:
        count = sum(item.review_required for item in items)
        return f"{shown} Требуют проверки человеком: {count}."
    if status is ResearchReportStatus.PARTIAL:
        return f"{shown} Часть фактов ещё не установлена (см. предупреждения)."
    return f"{shown} Все показанные факты подкреплены источниками, проверка не требуется."
