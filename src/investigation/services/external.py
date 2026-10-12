"""Retrievers of sources outside the database: one configured source search each.

What is found outside stays the evidence of this investigation: nothing is stored,
extracted or grouped (ADR 0027). A post outside says a text has these words; it is
never a fact and never decides who the subject is.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ingestion.search import SourceSearch, SourceSearchQuery
from investigation.models import EvidenceItem, InvestigationQuery, RetrieverCapabilities
from investigation.services.retrieval import TEXT_FACTS

EXTERNAL_RESULTS = 10


class SourceSearchRetriever:
    """A configured source's own search, asked by words: the name and the first of the
    words looked for, as a person would type them into its search box."""

    def __init__(self, search: SourceSearch, *, source: str, limit: int = EXTERNAL_RESULTS) -> None:
        self._search = search
        self._limit = limit
        self.name = f"{source}:{search.source}"
        self.capabilities = RetrieverCapabilities(
            source=source,
            local=False,
            network=True,
            kinds=frozenset({"keywords"}),
            facts=TEXT_FACTS,
            source_types=frozenset({"external"}),
        )

    async def search(self, query: InvestigationQuery) -> list[EvidenceItem]:
        if query.kind != "keywords":
            return []
        # A site's search box reads every word as required: the name and one word.
        words = " ".join(part for part in (query.name, *query.terms[:1]) if part)
        found = await self._search.search(SourceSearchQuery(text=words, limit=self._limit))
        now = datetime.now(UTC)
        return [
            EvidenceItem(
                source_type="external",
                source_id=self.name,
                source_name=item.source_name,
                external_id=item.external_id,
                url=item.url,
                title=item.title,
                text=item.text,
                published_at=item.published_at,
                retrieved_at=now,
                score_type="source_order",
                leads=() if query.fact is None else (query.fact,),
            )
            for item in found
        ]
