"""Report factuality invariant (ADR 0014).

Every fact in a `ResearchReport` must come from the deterministic
`ResearchService` result it was built from, and every source-derived claim must
trace back to stored data:

    claim → citation → evidence of this person → parsed article span → source document URL

A violation is a product bug (or a hallucination if an LLM ever wrote report
text), never a data gap: data gaps are warnings/review, not unsupported claims.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.orm_models import ParsedArticleRecord, SourceDocument
from research.models import PersonResearchResult
from research.reports.models import (
    ResearchClaim,
    ResearchClaimBasis,
    ResearchClaimType,
    ResearchReport,
    ResearchReportItem,
)


@dataclass(frozen=True)
class ProvenanceProblem:
    person_id: int | None
    claim_type: str | None
    problem: str


def verify_report_provenance(
    session: Session, report: ResearchReport, results: list[PersonResearchResult]
) -> list[ProvenanceProblem]:
    by_person = {result.person.id: result for result in results}
    problems: list[ProvenanceProblem] = []
    article_ids = {
        citation.article_id
        for item in report.items
        for claim in item.claims
        for citation in claim.citations
    } | {citation.article_id for item in report.items for citation in item.citations}
    articles = {
        article_id: (text, url)
        for article_id, text, url in session.execute(
            select(ParsedArticleRecord.id, ParsedArticleRecord.text, SourceDocument.canonical_url)
            .join(SourceDocument, SourceDocument.id == ParsedArticleRecord.document_id)
            .where(ParsedArticleRecord.id.in_(article_ids))
        ).all()
    }

    for item in report.items:
        result = by_person.get(item.person_id)
        if result is None:
            problems.append(
                ProvenanceProblem(item.person_id, None, "person is not in the research result")
            )
            continue
        problems.extend(_item_fact_problems(item.person_id, item, result))
        for claim in item.claims:
            problems.extend(_claim_problems(item.person_id, claim, result, articles))
    return problems


def _item_fact_problems(
    person_id: int, item: ResearchReportItem, result: PersonResearchResult
) -> list[ProvenanceProblem]:
    problems: list[ProvenanceProblem] = []
    persecution = result.persecution
    if item.persecution_status != (None if persecution is None else persecution.status):
        problems.append(ProvenanceProblem(person_id, None, "persecution status differs"))
    rf = result.rosfinmonitoring
    if item.rosfinmonitoring_status != (None if rf is None else rf.status):
        problems.append(ProvenanceProblem(person_id, None, "Rosfinmonitoring status differs"))
    result_events = {event.event_id for event in result.events}
    if not {event.event_id for event in item.events} <= result_events:
        problems.append(ProvenanceProblem(person_id, None, "report lists an unknown event"))
    return problems


def _claim_problems(
    person_id: int,
    claim: ResearchClaim,
    result: PersonResearchResult,
    articles: dict[int, tuple[str, str]],
) -> list[ProvenanceProblem]:
    kind = claim.claim_type.value
    problems: list[ProvenanceProblem] = []
    if not claim.supported:
        problems.append(ProvenanceProblem(person_id, kind, "source claim without citation"))
    if claim.claim_type is ResearchClaimType.PERSECUTION_CLASSIFICATION and (
        result.persecution is None or claim.classification_id != result.persecution.id
    ):
        problems.append(ProvenanceProblem(person_id, kind, "classification is not the result's"))
    if claim.claim_type is ResearchClaimType.ROSFINMONITORING_STATUS and (
        result.rosfinmonitoring is None
        or claim.basis is not ResearchClaimBasis.ROSFINMONITORING_SNAPSHOT
        or claim.snapshot_id != result.rosfinmonitoring.snapshot_id
    ):
        problems.append(ProvenanceProblem(person_id, kind, "snapshot is not the result's"))
    if claim.claim_type is ResearchClaimType.EVENT and claim.event_id not in {
        event.event_id for event in result.events
    }:
        problems.append(ProvenanceProblem(person_id, kind, "event is not the result's"))

    evidence = {
        (item.article_id, item.extraction_run_id, item.start_offset, item.end_offset)
        for item in result.evidence
    }
    source_urls = {source.article_id: source.url for source in result.sources}
    for citation in claim.citations:
        key = (
            citation.article_id,
            citation.extraction_run_id,
            citation.start_offset,
            citation.end_offset,
        )
        if key not in evidence:
            problems.append(ProvenanceProblem(person_id, kind, "citation is not this person's"))
            continue
        stored = articles.get(citation.article_id)
        if stored is None:
            problems.append(ProvenanceProblem(person_id, kind, "cited article does not exist"))
            continue
        text, url = stored
        if text[citation.start_offset : citation.end_offset] != citation.text:
            problems.append(ProvenanceProblem(person_id, kind, "citation text is not the span"))
        if citation.url != url or source_urls.get(citation.article_id) != url:
            problems.append(ProvenanceProblem(person_id, kind, "citation URL is not the source's"))
    return problems
