"""An investigation over a Telegram channel's own search as an outside source: the real
service, the real graph, fake retrievers. No channel is asked.

What is held: a post the database stores and the channel's search found again is one
piece of evidence, the publication's, with the facts the post was looked up for; and a
post is a lead of the fact it was looked up for only when it says something of that fact
about this person — a lead ends the search for its fact.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from ingestion.search import ExternalEvidence, SourceSearchQuery
from investigation.models import (
    EvidenceItem,
    InvestigationQuery,
    InvestigationResult,
    QueryKind,
    RetrieverCapabilities,
)
from investigation.service import InvestigationService
from investigation.services import evidence as evidence_rules
from investigation.services.external import SourceSearchRetriever

NAME = "Мария Бонцлер"
SUBJECT_KEY = "мария бонцлер"
DOSSIER = frozenset({"role", "charges", "politics", "rf_list", "latest_news"})
Answers = Callable[[InvestigationQuery], list[EvidenceItem]]
EvidenceAnswers = Callable[[SourceSearchQuery], list[ExternalEvidence]]
DOSSIER_QUESTION = "Мария Бонцлер статья"
POST = "https://t.me/ovdinfolive/5"
STORED_POST = f"{POST}?embed=1&mode=tme"
PUBLISHED = "Марию Бонцлер обвиняют по статье 207.3 УК РФ, суд оставил под стражей."


class FakeSearch:
    """A `SourceSearch` the run asks as it would ask a channel."""

    def __init__(self, source: str, answers: EvidenceAnswers | None = None) -> None:
        self.source = source
        self.title = source
        self._answers = answers or (lambda query: [])
        self.calls: list[SourceSearchQuery] = []

    async def search(self, query: SourceSearchQuery) -> list[ExternalEvidence]:
        self.calls.append(query)
        return self._answers(query)


class FakeRetriever:
    """A local retriever of the run, over lists the test holds."""

    def __init__(
        self,
        name: str,
        answers: Answers,
        *,
        kinds: Sequence[QueryKind],
        facts: frozenset[str] = frozenset(),
    ) -> None:
        self.name = name
        self.capabilities = RetrieverCapabilities(
            source="local", local=True, network=False, kinds=frozenset(kinds), facts=facts
        )
        self._answers = answers

    async def search(self, query: InvestigationQuery) -> list[EvidenceItem]:
        return self._answers(query)


def fact(name: str, value: str) -> EvidenceItem:
    return EvidenceItem(
        source_type="entity_group",
        source_id="entity_groups",
        entity_key=SUBJECT_KEY,
        text=f"{name}: {value}",
        fact=name,
        value=value,
    )


# Everything but the charges: the follow-up looks for them in the texts, and the channel
# is one of the places it looks.
KNOWN = [
    EvidenceItem(
        source_type="entity_group",
        source_id="entity_groups",
        entity_key=SUBJECT_KEY,
        text=f"{NAME}: 3 упоминания",
        fact="identity",
        value=NAME,
    ),
    fact("role", "figurant"),
    fact("politics", "political"),
    fact("rf_list", "full"),
    fact("latest_news", "arrest"),
]


def stored_post(text: str = PUBLISHED, *, url: str = STORED_POST) -> EvidenceItem:
    return EvidenceItem(
        source_type="publication",
        source_id="publications_fts",
        source_name="ОВД-Инфо LIVE",
        url=url,
        article_id=11,
        title="Суд над адвокатом",
        span_start=0,
        span_end=len(text),
        text=text,
        published_at=datetime(2026, 9, 25, tzinfo=UTC),
        score=0.4,
        score_type="ts_rank",
    )


def people(*extra: EvidenceItem) -> FakeRetriever:
    """The database's own person, and publications of it the local search holds."""
    return FakeRetriever(
        "entity_groups",
        lambda query: [*KNOWN, *extra] if query.kind == "person" else [],
        kinds=("person", "publication_terms"),
        facts=DOSSIER,
    )


def channel(text: str = PUBLISHED, *, url: str = POST) -> SourceSearchRetriever:
    def answers(query: SourceSearchQuery) -> list[ExternalEvidence]:
        return [
            ExternalEvidence(
                source="tg-ovdinfolive",
                source_name="ОВД-Инфо LIVE",
                external_id="5",
                url=url,
                text=text,
                published_at=datetime(2026, 9, 25, tzinfo=UTC),
                title="Суд над адвокатом",
            )
        ]

    return SourceSearchRetriever(FakeSearch("tg-ovdinfolive", answers), source="telegram")


def run(retrievers: Sequence[object], question: str, **ask: object) -> InvestigationResult:
    service = InvestigationService(retrievers=list(retrievers))  # type: ignore[arg-type]
    return asyncio.run(service.investigate(question, **ask))  # type: ignore[arg-type]


def asked(retriever: SourceSearchRetriever) -> list[str]:
    search = retriever._search
    assert isinstance(search, FakeSearch)
    return [call.text for call in search.calls]


def posts(result: InvestigationResult) -> list[EvidenceItem]:
    return [item for item in result.evidence if item.url and item.url.startswith(POST)]


def test_a_channel_post_stays_one_quote_with_the_publication_the_database_holds() -> None:
    """The same post has two addresses: the database stores the embed view it read
    (`?embed=1&mode=tme`), the channel's own search answers with the bare one. They are
    one post, so the run shows it once and the fact it was looked up for stands on the
    publication."""
    result = run([people(stored_post()), channel()], DOSSIER_QUESTION)

    (shown,) = posts(result)
    assert shown.article_id == 11
    assert shown.leads == ("charges",)


def test_a_post_the_database_does_not_hold_stays_the_channel_own() -> None:
    result = run([people(), channel()], DOSSIER_QUESTION)

    (shown,) = posts(result)
    assert shown.article_id is None
    assert shown.source_id == "telegram:tg-ovdinfolive"
    assert shown.leads == ("charges",)


def test_a_post_found_by_the_database_and_the_channel_counts_once_in_each_list() -> None:
    """Both lists a post stood in rank it, each once: under a ceiling of one it is the
    post the database alone found that gives way."""
    found_alone = stored_post("Мария Бонцлер выступила в суде.", url="https://t.me/ovdinfolive/4")
    found_alone = found_alone.model_copy(update={"article_id": 10, "query": "q", "round": 0})
    stored = stored_post().model_copy(update={"query": "q", "round": 0})
    copy = EvidenceItem(
        source_type="external",
        source_id="telegram:tg-ovdinfolive",
        source_name="ОВД-Инфо LIVE",
        external_id="5",
        url=POST,
        text=PUBLISHED,
        query="q",
        round=0,
    )

    scores = evidence_rules.rrf_scores([found_alone, stored, copy, copy])
    shown, cut = evidence_rules.merge([found_alone, stored, copy], limit=1)

    # The local list ranked the other post first; the channel's list is the second vote.
    assert scores[("article", 11)] == 1 / (evidence_rules.RRF_K + 2) + 1 / (
        evidence_rules.RRF_K + 1
    )
    assert [item.article_id for item in shown] == [11]
    assert cut == 1
