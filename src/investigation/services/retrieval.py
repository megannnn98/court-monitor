"""Where evidence comes from. The graph knows this interface and nothing of a backend.

A retriever has a name, says what it can be asked (`RetrieverCapabilities`) and
searches. The retrievers given to `InvestigationService` are its registry; the planner
asks each only what its capabilities allow. Local ones read the database through
`ResultsService`:

| retriever             | backend                          | answers                       |
|-----------------------|----------------------------------|-------------------------------|
| `entity_groups`       | entity groups and their verdicts | person, publication_terms     |
| `stored_publications` | text of stored publications      | person                        |
| `publications_fts`    | PostgreSQL full-text search      | keywords                      |
| `unnamed_figurants`   | unnamed figurants and the list   | unnamed                       |

A source outside the database is one more object of this shape (`services.external`).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Iterator, Sequence
from typing import Protocol

from investigation.models import (
    EvidenceItem,
    InvestigationQuery,
    QueryKind,
    RetrieverCapabilities,
)
from investigation.names import patronymic_key
from results.service import PublicationQuote, RankedPublication, ResultsService

ENTITY_CANDIDATES = 5
EVIDENCE_QUOTES = 20
TERM_QUOTES = 5
STORED_QUOTES = 10
RANKED_QUOTES = 10
LIST_CANDIDATES = 10
PUBLICATION_PEOPLE = 20
# How sure a name match is: every word is a whole word of the name, or only begins one.
EXACT_NAME = 1.0
DECLINED_NAME = 0.6
# The facts a quote of a publication can be a lead for.
TEXT_FACTS = frozenset({"role", "charges", "latest_news", "corroboration"})
LOCAL = "local"


class InvestigationRetriever(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def capabilities(self) -> RetrieverCapabilities: ...

    async def search(self, query: InvestigationQuery) -> list[EvidenceItem]: ...


class UnknownSourceError(ValueError):
    """A source the registry does not hold was asked for: refused, never ignored."""


class RetrieverRegistry:
    """The retrievers of a service by name. Built once by the composition root; nothing a
    run reads (a question, a publication, a model's answer) adds to it."""

    def __init__(self, retrievers: Iterable[InvestigationRetriever]) -> None:
        self._by_name: dict[str, InvestigationRetriever] = {}
        for retriever in retrievers:
            if retriever.name in self._by_name:
                raise ValueError(f"two retrievers named {retriever.name!r}")
            self._by_name[retriever.name] = retriever

    def __iter__(self) -> Iterator[InvestigationRetriever]:
        return iter(self._by_name.values())

    def __len__(self) -> int:
        return len(self._by_name)

    def get(self, name: str) -> InvestigationRetriever:
        return self._by_name[name]

    @property
    def sources(self) -> set[str]:
        """What `--source` may name: a source (`local`, `telegram`) or one retriever."""
        return {r.capabilities.source for r in self} | set(self._by_name)

    def check_sources(self, sources: Sequence[str]) -> None:
        unknown = sorted(set(sources) - self.sources)
        if unknown:
            known = ", ".join(sorted(self.sources))
            raise UnknownSourceError(f"unknown source: {', '.join(unknown)} (known: {known})")

    def allowed(
        self, *, local_only: bool, sources: Sequence[str] = ()
    ) -> list[InvestigationRetriever]:
        """The retrievers a run may ask: local ones alone in a local-only run, and only
        those of the sources the run keeps to, if it keeps to some."""
        self.check_sources(sources)
        wanted = set(sources)
        return [
            retriever
            for retriever in self
            if (retriever.capabilities.local or not local_only)
            and (not wanted or retriever.name in wanted or retriever.capabilities.source in wanted)
        ]


def can_answer(retriever: InvestigationRetriever, query: InvestigationQuery, purpose: str) -> bool:
    """The retriever takes this kind of query, and what it is asked for is something it
    can bring: the identity of whom it is asked about, or one of its facts."""
    capabilities = retriever.capabilities
    if query.kind not in capabilities.kinds:
        return False
    return purpose == "identity" or purpose in capabilities.facts


def _publication(
    quote: PublicationQuote, *, source_id: str, entity_key: str | None, lead: str | None = None
) -> EvidenceItem:
    return EvidenceItem(
        source_type="publication",
        source_id=source_id,
        source_name=quote.source,
        url=quote.url,
        article_id=quote.article_id,
        title=quote.title or None,
        entity_key=entity_key,
        span_start=quote.span_start,
        span_end=quote.span_end,
        text=quote.quote,
        published_at=quote.published_at,
        leads=() if lead is None else (lead,),
    )


def _ranked(
    found: RankedPublication, *, source_id: str, entity_key: str | None, lead: str | None
) -> EvidenceItem:
    return EvidenceItem(
        source_type="publication",
        source_id=source_id,
        source_name=found.source,
        url=found.url,
        article_id=found.article_id,
        title=found.title or None,
        entity_key=entity_key,
        span_start=found.span_start,
        span_end=found.span_end,
        text=found.quote,
        published_at=found.published_at,
        score=found.rank,
        score_type="ts_rank",
        leads=() if lead is None else (lead,),
    )


class LocalDatabaseRetriever:
    """The person as the pipeline knows them: the entity group, its verdicts, the places
    the publications name them, and what its record says of who they are."""

    name = "entity_groups"
    capabilities = RetrieverCapabilities(
        source=LOCAL,
        local=True,
        kinds=frozenset({"person", "publication_terms"}),
        facts=frozenset({"role", "charges", "politics", "rf_list", "latest_news"}),
        source_types=frozenset({"entity_group", "publication"}),
    )

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
        if query.kind != "person":
            return []
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
                score=EXACT_NAME if hit.exact else DECLINED_NAME,
                score_type="exact_name" if hit.exact else "declined_name",
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
        items.extend(self._profile(key))
        items.extend(
            _publication(quote, source_id=self.name, entity_key=key)
            for quote in self._results.entity_evidence(key, limit=EVIDENCE_QUOTES)
        )
        return items

    def _profile(self, key: str) -> list[EvidenceItem]:
        """The forms of the name with a patronymic, the regions, the list entries: what
        tells whether the group holds one person."""
        profile = self._results.entity_profile(key)
        if profile is None:
            return []
        items: list[EvidenceItem] = []
        seen: set[str] = set()
        for form, mentions in profile.variants:
            for word in form.split():
                father = patronymic_key(word)
                if father is not None and father not in seen:
                    seen.add(father)
                    items.append(
                        EvidenceItem(
                            source_type="entity_group",
                            source_id=self.name,
                            entity_key=key,
                            text=f"форма имени в публикациях: {form} (упоминаний: {mentions})",
                            fact="patronymic",
                            value=father,
                        )
                    )
        for region, publications in profile.regions:
            items.append(
                EvidenceItem(
                    source_type="entity_group",
                    source_id=self.name,
                    entity_key=key,
                    text=f"регион карточек реестра: {region} (публикаций: {publications})",
                    fact="region",
                    value=region,
                )
            )
        for full_name, birth_date in profile.rf_entries:
            items.append(
                EvidenceItem(
                    source_type="rf_entry",
                    source_id=self.name,
                    entity_key=key,
                    text=f"запись перечня, совпавшая по имени: {full_name}, {birth_date}",
                    fact="rf_entry",
                    value=f"{full_name} {birth_date}",
                )
            )
        return items


class StoredPublicationsRetriever:
    """Stored publications whose text holds the name, whether or not an entity group
    was made of them. A quote from here says a text names somebody of this name; it does
    not say it is the same person."""

    name = "stored_publications"
    capabilities = RetrieverCapabilities(
        source=LOCAL,
        local=True,
        kinds=frozenset({"person"}),
        source_types=frozenset({"publication"}),
    )

    def __init__(self, results: ResultsService) -> None:
        self._results = results

    async def search(self, query: InvestigationQuery) -> list[EvidenceItem]:
        if query.kind != "person":
            return []
        quotes = await asyncio.to_thread(
            self._results.stored_publication_quotes, query.name, limit=STORED_QUOTES
        )
        return [_publication(quote, source_id=self.name, entity_key=None) for quote in quotes]


class LexicalPublicationsRetriever:
    """Ranked full-text search of the stored publications (PostgreSQL FTS): the name and
    any one of the words, in any order and any case of the noun."""

    name = "publications_fts"
    capabilities = RetrieverCapabilities(
        source=LOCAL,
        local=True,
        kinds=frozenset({"keywords"}),
        facts=TEXT_FACTS,
        supports_date_range=True,
        source_types=frozenset({"publication"}),
    )

    def __init__(self, results: ResultsService, *, limit: int = RANKED_QUOTES) -> None:
        self._results = results
        self._limit = limit

    async def search(self, query: InvestigationQuery) -> list[EvidenceItem]:
        if query.kind != "keywords":
            return []
        found = await asyncio.to_thread(
            lambda: self._results.search_publications(
                name=query.name,
                any_of=query.terms,
                limit=self._limit,
                published_from=query.published_from,
                published_to=query.published_to,
            )
        )
        # A ranked hit says a text has these words; it is the person's only if they
        # were named by key, and then only as a lead.
        return [
            _ranked(item, source_id=self.name, entity_key=None, lead=query.fact) for item in found
        ]


class UnnamedFigurantRetriever:
    """The person a publication does not name: the clues the pipeline read of them, the
    entries of the Rosfinmonitoring list those clues fit, the people of the database
    named in the same publication. Structured: each item says what it states."""

    name = "unnamed_figurants"
    capabilities = RetrieverCapabilities(
        source=LOCAL,
        local=True,
        kinds=frozenset({"unnamed", "publication_people"}),
        facts=frozenset({"clues", "candidates"}),
        source_types=frozenset({"unnamed", "rf_entry", "entity_group"}),
    )

    def __init__(self, results: ResultsService) -> None:
        self._results = results

    async def search(self, query: InvestigationQuery) -> list[EvidenceItem]:
        if query.kind == "publication_people":
            return await asyncio.to_thread(self._people, query.article_ids)
        if query.kind != "unnamed" or query.article_id is None:
            return []
        return await asyncio.to_thread(self._search, query.article_id)

    def _people(self, article_ids: Sequence[int]) -> list[EvidenceItem]:
        """The people the pipeline grouped from these publications' mentions."""
        return [
            EvidenceItem(
                source_type="entity_group",
                source_id=self.name,
                article_id=person.article_id,
                entity_key=person.key,
                title=person.name,
                text=(
                    f"{person.name} назван в публикации {person.article_id} "
                    f"(упоминаний: {person.mention_count})"
                ),
                published_at=person.published_at,
                fact="named_in_publication",
                value=",".join(person.charges),
            )
            for person in self._results.people_of_publications(
                list(article_ids), limit=PUBLICATION_PEOPLE
            )
        ]

    def _search(self, article_id: int) -> list[EvidenceItem]:
        clues = self._results.figurant_clues(article_id)
        if clues is None:
            return []
        items: list[EvidenceItem] = []
        for clue in clues:
            told = {
                "age": None if clue.age is None else str(clue.age),
                "gender": clue.gender,
                "place": clue.place or None,
                "initial": clue.initial,
                "articles": ", ".join(clue.articles) or None,
                "event": clue.event_type,
                "published_on": clue.published_at.date().isoformat() if clue.published_at else None,
            }
            items.append(
                EvidenceItem(
                    source_type="unnamed",
                    source_id=self.name,
                    source_name=clue.source,
                    url=clue.url,
                    article_id=clue.article_id,
                    title=clue.title or None,
                    entity_key=f"figurant:{clue.key}",
                    span_start=clue.span_start,
                    span_end=clue.span_end,
                    text=clue.quote,
                    published_at=clue.published_at,
                    fact="clues",
                    value="; ".join(f"{key}={value}" for key, value in told.items() if value),
                )
            )
            listed = self._results.list_candidates(clue, shown=LIST_CANDIDATES)
            for candidate in listed.shown:
                if candidate.decision == "different":
                    continue
                signals = [f"age={candidate.age}"]
                if clue.gender:
                    signals.append("gender")
                if candidate.initial_match:
                    signals.append("initial")
                if candidate.place_match:
                    signals.append("birthplace")
                if candidate.decision == "same":
                    signals.append("confirmed")
                if candidate.removed:
                    signals.append("removed")
                items.append(
                    EvidenceItem(
                        source_type="rf_entry",
                        source_id=self.name,
                        entity_key=f"rf:{candidate.key}",
                        text=(
                            f"{candidate.full_name}, {candidate.birth_date.isoformat()}, "
                            f"{candidate.birth_place} — " + "; ".join(candidate.reasons)
                        ),
                        fact="candidates",
                        value="|".join([f"figurant:{clue.key}", candidate.key, *signals]),
                    )
                )
            if listed.total and not listed.shown:
                items.append(
                    EvidenceItem(
                        source_type="rf_entry",
                        source_id=self.name,
                        entity_key=f"figurant:{clue.key}",
                        text=(
                            f"в перечне {listed.total} записей этого возраста и пола; "
                            "признаков, чтобы сузить, нет"
                        ),
                        fact="candidates_total",
                        value=str(listed.total),
                    )
                )
        return items


def kinds_of(retrievers: Sequence[InvestigationRetriever]) -> set[QueryKind]:
    return {kind for retriever in retrievers for kind in retriever.capabilities.kinds}
