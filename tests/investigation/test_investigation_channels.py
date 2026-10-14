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

import pytest

from ingestion.search import ExternalEvidence, SourceSearchQuery
from investigation.models import (
    EvidenceItem,
    InvestigationQuery,
    InvestigationResult,
    QueryKind,
    RetrieverCapabilities,
)
from investigation.scenarios.unnamed_figurant import _fold, _mentions_place_articles
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


@pytest.mark.parametrize(
    "text",
    [
        # The name and nothing of the fact: the channel's search matches loosely, and is
        # sent the first word only.
        "Мария Бонцлер выступила в суде.",
        # A word that only begins like the one looked for.
        "Изменился статус Марии Бонцлер.",
        # An article of the Criminal Code — of somebody else.
        "Мария Бонцлер пришла в суд. Иванову вменяют ст. 280 УК РФ.",
        # Another country's code.
        "Марию Бонцлер обвинили по ч. 3 ст. 342 УК Беларуси.",
        # An article without the code it belongs to: which law it is, the post does not
        # say, so the articles are still looked for.
        "Марии Бонцлер предъявили обвинение по статье 280.",
    ],
)
def test_a_post_that_says_nothing_of_her_charges_is_no_lead_and_they_are_looked_for_again(
    text: str,
) -> None:
    outside = channel(text)

    result = run([people(), outside], DOSSIER_QUESTION, max_followups=2)

    (shown,) = posts(result)
    assert shown.leads == ()
    assert f"{NAME} статья" in asked(outside)
    assert f"{NAME} обвинение" in asked(outside)


def test_a_charge_about_her_is_a_lead_and_ends_the_search_for_it() -> None:
    outside = channel("Марии Бонцлер вменили две статьи, в том числе ч. 3 ст. 280 УК РФ.")

    result = run([people(), outside], DOSSIER_QUESTION, max_followups=2)

    (shown,) = posts(result)
    assert shown.leads == ("charges",)
    assert f"{NAME} обвинение" not in asked(outside)


# -- the figurant's own scenario -----------------------------------------------------------------

FIGURANT_QUESTION = "Установи фигуранта публикации 28191"
CLUE_POST = "https://t.me/tumen_news/7"
CLUE = EvidenceItem(
    source_type="unnamed",
    source_id="unnamed_figurants",
    article_id=28191,
    entity_key="figurant:fig-1",
    url=f"{CLUE_POST}?embed=1&mode=tme",
    title="Задержали жителя Тюмени",
    text="В Тюмени задержали 17-летнего жителя города.",
    published_at=datetime(2026, 9, 20, tzinfo=UTC),
    fact="clues",
    value="age=17; gender=m; place=Тюмень; articles=158.1",
)
CANDIDATE = EvidenceItem(
    source_type="rf_entry",
    source_id="unnamed_figurants",
    entity_key="rf:ИВАНОВ|ИВАН|2009-01-01",
    text="ИВАНОВ ИВАН, 2009-01-01, Тюмень",
    fact="candidates",
    value="figurant:fig-1;age=17;gender;birthplace",
)


def figurants() -> FakeRetriever:
    return FakeRetriever(
        "unnamed_figurants",
        lambda query: [CLUE, CANDIDATE] if query.kind == "unnamed" else [],
        kinds=("unnamed", "publication_people"),
        facts=frozenset({"clues", "candidates"}),
    )


def post(url: str, text: str) -> ExternalEvidence:
    return ExternalEvidence(
        source="tg-tumen_news",
        source_name="Тюмень новости",
        external_id=url.rsplit("/", 1)[-1],
        url=url,
        text=text,
        published_at=datetime(2026, 9, 21, tzinfo=UTC),
        title=None,
    )


def test_only_a_second_publication_about_the_case_corroborates_the_candidate() -> None:
    """The publication of the clue cannot confirm itself, however it came back: the
    channel's search answers with the very post the clue was read of, by its bare
    address. And a name with a place is what any list prints — a post has to speak of the
    case's article as well."""

    def answer(query: SourceSearchQuery) -> list[ExternalEvidence]:
        if not query.text.startswith("Иванов"):
            return []
        return [
            # The very publication of the clue, found again: not a second one.
            post(CLUE_POST, "Иванов из Тюмени задержан по части 4 статьи 158.1."),
            # A listing: the name and the place, nothing of the case.
            post("https://t.me/tumen_news/8", "Иванов Иван, Тюмень: поздравляем с юбилеем."),
            # Another case of the same man: an article that only begins like the case's.
            post(
                "https://t.me/tumen_news/10",
                "Иванов из Тюмени: другое дело по части 1 статьи 158.2.",
            ),
            # A second publication about the same case.
            post(
                "https://t.me/tumen_news/9", "Иванов из Тюмени: то же дело по части 4 статьи 158.1."
            ),
        ]

    outside = SourceSearchRetriever(FakeSearch("tg-tumen_news", answer), source="telegram")

    result = run([figurants(), outside], FIGURANT_QUESTION)

    refs = {
        item.url: item.ref
        for item in result.evidence
        if item.source_type == "external" and "corroboration" in item.leads
    }
    (candidate,) = result.candidates
    cited = {
        ref
        for signal in candidate.signals
        if signal.signal == "named_with_place"
        for ref in signal.evidence
    }
    assert cited == {refs["https://t.me/tumen_news/9"]}


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


@pytest.mark.parametrize(
    ("article", "text", "held"),
    [
        ("158.1", "то же дело по части 4 статьи 158.1.", True),
        ("158.1", "дело по ч. 1 ст. 158.2 УК РФ.", False),
        ("158.1", "по ст. 1158.1 и ст. 158.12", False),
        ("158", "по ст. 158.1 УК", False),
        # The model may write the case's article with its part; a post may write it with
        # no spaces at all.
        ("ч. 2 ст. 158.1", "по ч.2 ст.158.1 УК.", True),
    ],
)
def test_the_case_article_is_read_by_its_numbers(article: str, text: str, held: bool) -> None:
    assert _mentions_place_articles(_fold(text), article) is held
