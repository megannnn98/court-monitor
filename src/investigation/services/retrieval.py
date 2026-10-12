"""Where evidence comes from. The graph knows this interface and nothing of a backend.

Two retrievers ship, both over the local database through `ResultsService`:
`LocalDatabaseRetriever` reads the person's entity group (ADR 0024) and what the
pipeline decided of it; `StoredPublicationsRetriever` reads the stored publications by
their text. A retriever of another source (a site, a channel, a vector index) is one
more object with these two attributes and this one method.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Protocol

from investigation.models import EvidenceItem, InvestigationQuery
from results.service import PublicationQuote, ResultsService

ENTITY_CANDIDATES = 5
EVIDENCE_QUOTES = 20
TERM_QUOTES = 5
STORED_QUOTES = 10
# How sure a name match is: every word is a whole word of the name, or only begins one.
EXACT_NAME = 1.0
DECLINED_NAME = 0.6


class InvestigationRetriever(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def local(self) -> bool:
        """Reads only what is already stored here; asks nothing outside."""
        ...

    async def search(self, query: InvestigationQuery) -> list[EvidenceItem]: ...


def _publication(
    quote: PublicationQuote, *, source_id: str, entity_key: str | None, lead: str | None = None
) -> EvidenceItem:
    return EvidenceItem(
        source_type="publication",
        source_id=source_id,
        url=quote.url,
        article_id=quote.article_id,
        entity_key=entity_key,
        span_start=quote.span_start,
        span_end=quote.span_end,
        text=quote.quote,
        published_at=quote.published_at,
        leads=() if lead is None else (lead,),
    )


class LocalDatabaseRetriever:
    """The person as the pipeline knows them: the entity group, its verdicts, the places
    the publications name them."""

    name = "entity_groups"
    local = True

    def __init__(self, results: ResultsService) -> None:
        self._results = results

    async def search(self, query: InvestigationQuery) -> list[EvidenceItem]:
        return await asyncio.to_thread(self._search, query)

    def _search(self, query: InvestigationQuery) -> list[EvidenceItem]:
        if query.kind == "publication_terms":
            if query.entity_key is None:
                return []
            return [
                _publication(
                    quote, source_id=self.name, entity_key=query.entity_key, lead=query.fact
                )
                for quote in self._results.entity_publication_quotes(
                    query.entity_key, query.terms, limit=TERM_QUOTES
                )
            ]
        hits = self._results.find_entities(query.name, limit=ENTITY_CANDIDATES)
        items = [
            EvidenceItem(
                source_type="entity_group",
                source_id=self.name,
                entity_key=hit.key,
                text=(
                    f"{hit.name} — упоминаний: {hit.mention_count}, публикаций: {hit.article_count}"
                ),
                published_at=hit.last_published_at,
                confidence=EXACT_NAME if hit.exact else DECLINED_NAME,
                fact="identity",
                value=hit.name,
            )
            for hit in hits
        ]
        if len(hits) != 1:
            # Nobody, or several people: whose verdicts to read is not this code's to say.
            return items
        key = hits[0].key
        for stated in self._results.entity_facts(key) or []:
            items.append(
                EvidenceItem(
                    source_type="entity_group",
                    source_id=self.name,
                    entity_key=key,
                    text=stated.basis or f"{stated.fact}: {stated.value}",
                    published_at=stated.published_at,
                    fact=stated.fact,
                    value=stated.value,
                )
            )
        items.extend(
            _publication(quote, source_id=self.name, entity_key=key)
            for quote in self._results.entity_evidence(key, limit=EVIDENCE_QUOTES)
        )
        return items


class StoredPublicationsRetriever:
    """Stored publications whose text holds the name, whether or not an entity group
    was made of them. A quote from here says a text names somebody of this name; it does
    not say it is the same person."""

    name = "stored_publications"
    local = True

    def __init__(self, results: ResultsService) -> None:
        self._results = results

    async def search(self, query: InvestigationQuery) -> list[EvidenceItem]:
        if query.kind != "person":
            return []
        quotes = await asyncio.to_thread(
            self._results.stored_publication_quotes, query.name, limit=STORED_QUOTES
        )
        return [_publication(quote, source_id=self.name, entity_key=None) for quote in quotes]


def usable(
    retrievers: Sequence[InvestigationRetriever], *, local_only: bool
) -> list[InvestigationRetriever]:
    return [retriever for retriever in retrievers if retriever.local or not local_only]
