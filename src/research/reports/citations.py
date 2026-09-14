"""Citations from the provenance ResearchService already returns.

No second provenance pipeline: a citation is an existing `ResearchEvidence`
joined with the `ResearchSource` of its article. Evidence whose article is not
in the result's sources is not citable.
"""

from __future__ import annotations

from research.models import (
    PersonResearchResult,
    ResearchEvidence,
    ResearchEvidenceType,
    ResearchSource,
)
from research.reports.models import ResearchCitation


def _citation(evidence: ResearchEvidence, source: ResearchSource) -> ResearchCitation:
    return ResearchCitation(
        article_id=evidence.article_id,
        article_title=source.article_title,
        source_name=source.source_name,
        url=source.url,
        published_at=source.published_at,
        evidence_type=evidence.evidence_type,
        text=evidence.text,
        start_offset=evidence.start_offset,
        end_offset=evidence.end_offset,
        extraction_run_id=evidence.extraction_run_id,
        mention_id=evidence.mention_id,
        event_id=evidence.event_id,
    )


def citations(result: PersonResearchResult) -> list[ResearchCitation]:
    """All person-scoped evidence of the result that points to a known source."""
    sources = {source.article_id: source for source in result.sources}
    return [
        _citation(evidence, sources[evidence.article_id])
        for evidence in result.evidence
        if evidence.article_id in sources
    ]


def evidence_without_source(result: PersonResearchResult) -> list[ResearchEvidence]:
    known = {source.article_id for source in result.sources}
    return [evidence for evidence in result.evidence if evidence.article_id not in known]


def mention_citations(result: PersonResearchResult) -> list[ResearchCitation]:
    return [
        citation
        for citation in citations(result)
        if citation.evidence_type is ResearchEvidenceType.PERSON_MENTION
    ]


def event_citations(result: PersonResearchResult, event_id: int) -> list[ResearchCitation]:
    return [
        citation
        for citation in citations(result)
        if citation.evidence_type is ResearchEvidenceType.EVENT and citation.event_id == event_id
    ]
