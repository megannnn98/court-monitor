"""An investigation over the web as one more outside source: the real service, the real
graph, fake retrievers and a fake transport. No site is asked.

What is held: a key puts `web:tavily` in the registry and no key leaves it out; a run
reaches the web by the same keywords it reaches the database and the channels by, once
per query; what comes back is evidence with a ref and a lead, never a fact; a failure or
a timeout of the web is a warning and not the run; a page the local search found too
stands once. The registry itself is read over the test database.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session, sessionmaker
from support.mcp_server import served_tavily_mcp

from application import build_investigation_retrievers
from ingestion.search import ExternalEvidence, SourceSearchQuery
from ingestion.web_search import TavilyMcpSearch
from investigation.models import (
    EvidenceItem,
    InvestigationQuery,
    InvestigationResult,
    Limits,
    QueryKind,
    RetrieverCapabilities,
)
from investigation.runs import InMemoryRunStore
from investigation.scenarios.unnamed_figurant import WINDOW_DAYS
from investigation.service import InvestigationService, UnknownSourceError
from investigation.services.external import SourceSearchRetriever

NAME = "Мария Бонцлер"
SUBJECT_KEY = "мария бонцлер"
DOSSIER = frozenset({"role", "charges", "politics", "rf_list", "latest_news"})
TEXT = frozenset({"role", "charges", "latest_news", "corroboration"})
Answers = Callable[[InvestigationQuery], list[EvidenceItem]]
EvidenceAnswers = Callable[[SourceSearchQuery], list[ExternalEvidence]]
DOSSIER_QUESTION = "Мария Бонцлер статья"
FIGURANT_QUESTION = "Установи фигуранта публикации 28191"
PAGE = "https://news.example.test/boncler-1"
OTHER_PAGE = "https://other.example.test/ivanov"
PUBLISHED = "Марию Бонцлер обвиняют по статье 207.3 УК РФ, суд оставил под стражей."


# -- the fakes ----------------------------------------------------------------------------------


class FakeSearch:
    """A `SourceSearch` the run asks as it would ask a site: it holds its own answers and
    keeps every question it was asked."""

    def __init__(
        self,
        source: str,
        answers: EvidenceAnswers | None = None,
        *,
        fail: Exception | None = None,
        slow: float = 0.0,
    ) -> None:
        self.source = source
        self.title = source
        self._answers = answers or (lambda query: [])
        self._fail = fail
        self._slow = slow
        self.calls: list[SourceSearchQuery] = []

    async def search(self, query: SourceSearchQuery) -> list[ExternalEvidence]:
        self.calls.append(query)
        if self._slow:
            await asyncio.sleep(self._slow)
        if self._fail is not None:
            raise self._fail
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
        self.calls: list[InvestigationQuery] = []

    async def search(self, query: InvestigationQuery) -> list[EvidenceItem]:
        self.calls.append(query)
        return self._answers(query)


def fact(name: str, value: str, *, key: str = SUBJECT_KEY) -> EvidenceItem:
    return EvidenceItem(
        source_type="entity_group",
        source_id="entity_groups",
        entity_key=key,
        text=f"{name}: {value}",
        fact=name,
        value=value,
    )


# Everything but the charges: the follow-up looks for them in the texts, and the web is
# one of the places it looks.
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


def web_answer(
    text: str = PUBLISHED, *, url: str = PAGE, published: bool = True
) -> EvidenceAnswers:
    def answers(query: SourceSearchQuery) -> list[ExternalEvidence]:
        return [
            ExternalEvidence(
                source="tavily",
                source_name=url.removeprefix("https://").split("/")[0],
                external_id=url,
                url=url,
                text=text,
                published_at=datetime(2026, 9, 25, tzinfo=UTC) if published else None,
                title="Суд над адвокатом",
            )
        ]

    return answers


def stored_publication(
    text: str = PUBLISHED, *, url: str = PAGE, article_id: int = 7
) -> EvidenceItem:
    return EvidenceItem(
        source_type="publication",
        source_id="publications_fts",
        source_name="news",
        url=url,
        article_id=article_id,
        title="Суд над адвокатом",
        span_start=0,
        span_end=len(text),
        text=text,
        published_at=datetime(2026, 9, 25, tzinfo=UTC),
        score=0.4,
        score_type="ts_rank",
    )


def people(answers: Answers | None = None) -> FakeRetriever:
    return FakeRetriever(
        "entity_groups",
        answers or (lambda query: list(KNOWN) if query.kind == "person" else []),
        kinds=("person", "publication_terms"),
        facts=DOSSIER,
    )


def people_with(*extra: EvidenceItem) -> FakeRetriever:
    """The database's own person, and publications of it the local search holds."""
    return people(lambda query: [*KNOWN, *extra] if query.kind == "person" else [])


def fts(answers: Answers | None = None) -> FakeRetriever:
    return FakeRetriever(
        "publications_fts", answers or (lambda query: []), kinds=("keywords",), facts=TEXT
    )


def web(answers: EvidenceAnswers | None = None, **kwargs: object) -> SourceSearchRetriever:
    return SourceSearchRetriever(FakeSearch("tavily", answers, **kwargs), source="web")  # type: ignore[arg-type]


def channel(answers: EvidenceAnswers | None = None) -> SourceSearchRetriever:
    return SourceSearchRetriever(FakeSearch("tg-ovdinfolive", answers), source="telegram")


def search_of(retriever: SourceSearchRetriever) -> FakeSearch:
    search = retriever._search
    assert isinstance(search, FakeSearch)
    return search


def service(*retrievers: object, **kwargs: object) -> InvestigationService:
    return InvestigationService(retrievers=list(retrievers), **kwargs)  # type: ignore[arg-type]


def run(retrievers: Sequence[object], question: str, **ask: object) -> InvestigationResult:
    return asyncio.run(service(*retrievers).investigate(question, **ask))  # type: ignore[arg-type]


def page_items(result: InvestigationResult, url: str = PAGE) -> list[EvidenceItem]:
    return [item for item in result.evidence if item.url == url]


# -- the registry --------------------------------------------------------------------------------


def test_a_key_puts_the_web_in_the_registry_beside_the_database_and_the_channels(
    session_factory: sessionmaker[Session],
) -> None:
    with_web = build_investigation_retrievers(
        session_factory, env={"INVESTIGATION_TELEGRAM_CHANNELS": "none", "TAVILY_API_KEY": "key"}
    )

    (one,) = [retriever for retriever in with_web if retriever.name == "web:tavily"]
    assert one.capabilities.source == "web"
    assert one.capabilities.network and not one.capabilities.local
    # An addition, not a replacement: the database and the channels are still there.
    assert {retriever.name for retriever in with_web} == {
        "entity_groups",
        "stored_publications",
        "publications_fts",
        "unnamed_figurants",
        "web:tavily",
    }


def test_without_a_key_the_registry_holds_no_web(
    session_factory: sessionmaker[Session],
) -> None:
    built = build_investigation_retrievers(
        session_factory, env={"INVESTIGATION_TELEGRAM_CHANNELS": "none"}
    )

    assert [retriever.name for retriever in built] == [
        "entity_groups",
        "stored_publications",
        "publications_fts",
        "unnamed_figurants",
    ]
    assert "web" not in service(*built).sources and "web:tavily" not in service(*built).sources


def test_a_web_takes_the_timeout_of_the_runs_own_searches(
    session_factory: sessionmaker[Session],
) -> None:
    built = build_investigation_retrievers(
        session_factory,
        env={
            "INVESTIGATION_TELEGRAM_CHANNELS": "none",
            "TAVILY_API_KEY": "key",
            "INVESTIGATION_SEARCH_TIMEOUT_SECONDS": "7",
        },
    )

    (one,) = [retriever for retriever in built if retriever.name == "web:tavily"]
    assert isinstance(one, SourceSearchRetriever)
    search = one._search
    assert isinstance(search, TavilyMcpSearch)
    # The web waits no longer than any other search of the run.
    assert search.timeout_seconds == 7.0


# -- the flags of a run --------------------------------------------------------------------------


def test_a_local_only_run_never_reaches_the_web() -> None:
    outside = web(web_answer())

    result = run([people(), outside], DOSSIER_QUESTION, local_only=True)

    assert search_of(outside).calls == []
    assert result.usage.network_searches == 0


@pytest.mark.parametrize("source", ["web", "web:tavily"])
def test_the_web_can_be_named_and_kept_to_alone(source: str) -> None:
    outside = web(web_answer())

    result = run([people(), outside], DOSSIER_QUESTION, sources=[source])

    assert search_of(outside).calls
    assert {item.source_id for item in result.evidence} == {"web:tavily"}
    assert result.usage.local_searches == 0


def test_a_source_the_registry_does_not_hold_is_refused() -> None:
    with pytest.raises(UnknownSourceError, match="telegram"):
        run([people(), web(web_answer())], DOSSIER_QUESTION, sources=["telegram"])


# -- one query, every retriever ------------------------------------------------------------------


def test_one_keywords_query_reaches_the_database_the_channels_and_the_web() -> None:
    local_people, local, outside, chan = people(), fts(), web(web_answer()), channel()

    result = run([local_people, local, chan, outside], DOSSIER_QUESTION)

    keywords = [query for query in local.calls if query.kind == "keywords"]
    assert keywords
    # The same query, one search each: the planner does not search the web twice.
    assert len(search_of(outside).calls) == len(keywords)
    assert len(search_of(chan).calls) == len(keywords)
    # One search per retriever and query: none of them is asked the same one twice.
    assert result.usage.search_calls == sum(
        len(retriever.calls) for retriever in (local_people, local)
    ) + len(search_of(chan).calls) + len(search_of(outside).calls)
    # Each source is asked the words a person would type into its search box: the name
    # and the first of the words looked for.
    assert {call.text for call in search_of(outside).calls} == {
        " ".join(part for part in (query.name, *query.terms[:1]) if part) for query in keywords
    }
    assert {call.text for call in search_of(chan).calls} == {
        call.text for call in search_of(outside).calls
    }


def test_the_web_is_asked_once_per_query_though_the_run_has_rounds() -> None:
    outside = web(web_answer())

    run([people(), outside], DOSSIER_QUESTION, max_followups=2)

    asked = [call.text for call in search_of(outside).calls]
    assert asked and len(asked) == len(set(asked))


def test_a_text_that_only_names_her_is_no_lead_and_the_fact_is_looked_for_again() -> None:
    """A lead ends the search for its fact. A text found for the charges that says nothing
    of an article — the web matches loosely, and is sent the first word only — must not
    end it: the second words for the charges are still asked."""
    outside = web(web_answer("Мария Бонцлер выступила в суде."))

    result = run([people(), outside], DOSSIER_QUESTION, max_followups=2)

    (item,) = page_items(result)
    assert item.source_id == "web:tavily" and item.leads == ()
    asked = [call.text for call in search_of(outside).calls]
    assert f"{NAME} статья" in asked
    assert f"{NAME} обвинение" in asked


def test_a_charge_about_her_is_a_lead_and_the_fact_is_still_looked_up_until_it_is_verified() -> (
    None
):
    """«Вменили две статьи, в том числе ч. 3 ст. 280 УК РФ» is a charge about *this*
    person — the name stands next to the accusation and a Criminal Code article next to
    it, and the word of the fact may be declined («статьи» of «статья»). It is a *lead*:
    which text to read, not a fact, so the run keeps looking for the fact until something
    grounded has confirmed it. That is the whole point: a lead must never be what stops a
    follow-up that could still find the truth."""
    outside = web(web_answer("Марии Бонцлер вменили две статьи, в том числе ч. 3 ст. 280 УК РФ."))

    result = run([people(), outside], DOSSIER_QUESTION, max_followups=2)

    (item,) = page_items(result)
    assert item.leads == ("charges",)
    assert "статьи обвинения" in result.missing_information
    asked = [call.text for call in search_of(outside).calls]
    assert f"{NAME} статья" in asked and f"{NAME} обвинение" in asked


def test_a_word_that_only_begins_the_same_way_is_not_the_word_looked_for() -> None:
    """The rule the dictionary gives: a text that holds «статус» says nothing of the
    articles, so it is not a lead of them and the run keeps looking for them."""
    outside = web(web_answer("Изменился статус Марии Бонцлер."))

    result = run([people(), outside], DOSSIER_QUESTION, max_followups=2)

    (item,) = page_items(result)
    assert item.leads == ()
    asked = [call.text for call in search_of(outside).calls]
    assert f"{NAME} статья" in asked and f"{NAME} обвинение" in asked
    assert "статьи обвинения" in result.missing_information


def test_the_words_travel_to_tavily_in_the_body_and_the_key_in_the_header() -> None:
    with served_tavily_mcp([], expected_key="a-key") as (endpoint, recorder):
        outside = SourceSearchRetriever(
            TavilyMcpSearch("a-key", url=endpoint, base_delay_seconds=0), source="web"
        )

        run([people(), outside], DOSSIER_QUESTION)

    assert recorder.calls
    assert all(NAME in call["query"] for call in recorder.calls)
    assert set(recorder.authorizations) == {"Bearer a-key"}


def test_two_articles_of_one_feed_found_by_the_web_both_stand() -> None:
    """The same host and path, a different article of it. They are one document each, and
    neither is a repeat of the other — a key that dropped the query would keep the first
    and throw the second away, and with it everything it was looked up for."""
    outside = SourceSearchRetriever(
        FakeSearch(
            "tavily",
            lambda query: [
                ExternalEvidence(
                    source="tavily",
                    source_name="news.example.test",
                    external_id=f"news.example.test/news.php?id={number}",
                    url=f"https://news.example.test/news.php?id={number}",
                    text=f"Марию Бонцлер обвиняют по ч. 3 ст. 20{number} УК РФ.",
                    published_at=None,
                    title=f"Материал {number}",
                )
                for number in (1, 2)
            ],
        ),
        source="web",
    )

    result = run([people(), outside], DOSSIER_QUESTION)

    shown = sorted(str(item.url) for item in result.evidence if item.url and "news.php" in item.url)
    assert shown == [
        "https://news.example.test/news.php?id=1",
        "https://news.example.test/news.php?id=2",
    ]


def test_a_page_of_the_same_feed_but_another_article_is_the_web_s_own() -> None:
    """`site.ru/news.php?id=1` and `?id=2` are two articles of one feed. Dropping the query
    would make the second stand for the publication the database already holds and
    disappear into it — along with the facts it was looked up for."""
    stored = stored_publication(
        "Первый материал о Марии Бонцлер.", url="https://news.example.test/news.php?id=1"
    )
    outside = SourceSearchRetriever(
        FakeSearch(
            "tavily",
            web_answer(
                "Марию Бонцлер обвиняют по ч. 3 ст. 207.3 УК РФ.",
                url="https://news.example.test/news.php?id=2",
            ),
        ),
        source="web",
    )

    result = run([people_with(stored), outside], DOSSIER_QUESTION)

    shown = {item.url for item in result.evidence}
    assert "https://news.example.test/news.php?id=2" in shown
    (found,) = [item for item in result.evidence if item.url and item.url.endswith("id=2")]
    # Its own lead is its own: the publication of the database is a different article, and
    # is not credited with what this one was looked up for.
    assert found.leads == ("charges",)
    local = [item for item in result.evidence if item.article_id == 7]
    assert [item.leads for item in local] == [()]


# -- the dossier of a person ---------------------------------------------------------------------


def test_a_web_text_that_is_nobody_s_lead_ranks_after_the_local_quotes() -> None:
    from investigation.services.evidence import merge

    local = EvidenceItem(
        source_type="publication",
        source_id="publications_fts",
        url="https://news.example.test/local",
        article_id=7,
        span_start=0,
        span_end=10,
        text="Суд оставил под стражей.",
    )
    unasked = EvidenceItem(
        source_type="external",
        source_id="web:tavily",
        external_id="x",
        url="https://other.example.test/a",
        text="Мария Бонцлер пишет о деле.",
        score_type="source_order",
    )
    asked = unasked.model_copy(
        update={
            "url": "https://other.example.test/b",
            "external_id": "y",
            "leads": ("charges",),
            "text": "Марии Бонцлер вменили статью 280.",
        }
    )

    ordered, _ = merge([unasked, local, asked], limit=10, external_limit=10)

    assert [item.url for item in ordered] == [
        "https://news.example.test/local",
        "https://other.example.test/b",
        "https://other.example.test/a",
    ]


def test_no_number_of_web_results_pushes_out_one_local_quote() -> None:
    """The database's own ceiling stays the database's own. What was found outside is
    shown beside it, up to its own small ceiling, however much of it there is."""
    from investigation.services.evidence import merge

    local = [
        EvidenceItem(
            source_type="publication",
            source_id="publications_fts",
            url=f"https://news.example.test/local-{number}",
            article_id=number,
            span_start=0,
            span_end=10,
            text=f"Цитата {number} о деле.",
        )
        for number in range(40)
    ]

    for amount in (1, 10, 50, 200):
        outside = [
            EvidenceItem(
                source_type="external",
                source_id="web:tavily",
                external_id=f"w{number}",
                url=f"https://other.example.test/{number}",
                text=f"Мария Бонцлер упоминание {number}.",
                score_type="source_order",
            )
            for number in range(amount)
        ]

        shown, cut = merge([*outside, *local], limit=40, external_limit=10)

        kept_local = [item for item in shown if item.source_type == "publication"]
        kept_outside = [item for item in shown if item.source_type == "external"]
        assert len(kept_local) == 40, f"{amount} веб-результатов съели локальные"
        assert len(kept_outside) == min(10, amount)
        assert cut == max(0, amount - 10)


def test_among_the_ten_shown_from_outside_the_leads_come_first() -> None:
    """Ten texts found outside are a choice, and the ones the run actually asked for are
    the ten worth showing."""
    from investigation.services.evidence import merge

    asked = [
        EvidenceItem(
            source_type="external",
            source_id="web:tavily",
            external_id=f"a{number}",
            url=f"https://other.example.test/lead-{number}",
            text=f"Марии Бонцлер вменили статью {number}.",
            leads=("charges",),
            score_type="source_order",
        )
        for number in range(6)
    ]
    unasked = [
        EvidenceItem(
            source_type="external",
            source_id="web:tavily",
            external_id=f"u{number}",
            url=f"https://other.example.test/other-{number}",
            text=f"Мария Бонцлер упоминание {number}.",
            score_type="source_order",
        )
        for number in range(20)
    ]

    shown, _ = merge([*unasked, *asked], limit=40, external_limit=10)

    outside = [item for item in shown if item.source_type == "external"]
    assert len(outside) == 10
    assert all(item.leads for item in outside[:6])
    assert not any(item.leads for item in outside[6:])


def test_without_a_ceiling_of_its_own_nothing_from_outside_is_shown() -> None:
    """Before there was a web there was no second ceiling and nothing outside was shown;
    the two ceilings do not take each other's place in either direction."""
    from investigation.services.evidence import merge

    local = [
        EvidenceItem(
            source_type="publication",
            source_id="publications_fts",
            url=f"https://news.example.test/local-{number}",
            article_id=number,
            span_start=0,
            span_end=10,
            text=f"Цитата {number} о деле.",
        )
        for number in range(3)
    ]
    outside = [
        EvidenceItem(
            source_type="external",
            source_id="web:tavily",
            external_id=f"w{number}",
            url=f"https://other.example.test/{number}",
            text=f"Мария Бонцлер упоминание {number}.",
            score_type="source_order",
        )
        for number in range(5)
    ]

    shown, cut = merge([*outside, *local], limit=40, external_limit=0)

    assert all(item.source_type == "publication" for item in shown)
    assert cut == 5


def test_a_telegram_quote_is_not_squeezed_by_the_webs_ceiling() -> None:
    """The web's ten are the web's. A channel's quotes keep the one ceiling they had
    before there was a web, and are shown under it however much the web brought."""
    from investigation.services.evidence import merge

    channel = [
        EvidenceItem(
            source_type="external",
            source_id="telegram:tg-pzk_memorial",
            external_id=f"t{number}",
            url=f"https://t.me/pzk_memorial/{number}",
            text=f"Мария Бонцлер цитата {number} о деле.",
            score_type="source_order",
        )
        for number in range(30)
    ]
    web = [
        EvidenceItem(
            source_type="external",
            source_id="web:tavily",
            external_id=f"w{number}",
            url=f"https://other.example.test/{number}",
            text=f"Мария Бонцлер упоминание {number}.",
            score_type="source_order",
        )
        for number in range(25)
    ]

    shown, cut = merge([*web, *channel], limit=40, external_limit=10)

    kept_channel = [item for item in shown if item.source_id.startswith("telegram:")]
    kept_web = [item for item in shown if item.source_id.startswith("web:")]
    assert len(kept_channel) == 30, "потолок веба сжал каналы"
    assert len(kept_web) == 10
    assert cut == 15


def test_the_web_ceiling_follows_the_retriever_not_the_providers_name() -> None:
    """`web:` is the retriever's kind. Another provider's web search is under the same
    ceiling, and a provider that is not the web is not under it whatever it is called."""
    from investigation.services.evidence import merge

    def item(source_id: str, number: int) -> EvidenceItem:
        return EvidenceItem(
            source_type="external",
            source_id=source_id,
            external_id=f"{source_id}-{number}",
            url=f"https://example.test/{source_id}/{number}",
            text="Мария Бонцлер упоминание.",
            score_type="source_order",
        )

    shown, _ = merge(
        [item("web:another-provider", n) for n in range(30)]
        + [item("telegram:tavily-lookalike", n) for n in range(30)],
        limit=40,
        external_limit=10,
    )

    assert len([i for i in shown if i.source_id == "web:another-provider"]) == 10
    assert len([i for i in shown if i.source_id.startswith("telegram:")]) == 30


def test_a_web_text_nobody_looked_for_is_dropped_first_when_the_ceiling_cuts() -> None:
    from investigation.services.evidence import merge

    local = [
        EvidenceItem(
            source_type="publication",
            source_id="publications_fts",
            url=f"https://news.example.test/local-{number}",
            article_id=number,
            span_start=0,
            span_end=10,
            text=f"Цитата {number} о деле.",
        )
        for number in range(3)
    ]
    noise = [
        EvidenceItem(
            source_type="external",
            source_id="web:tavily",
            external_id=f"n{number}",
            url=f"https://other.example.test/{number}",
            text=f"Мария Бонцлер упоминание {number}.",
            score_type="source_order",
        )
        for number in range(3)
    ]

    ordered, cut = merge([*noise, *local], limit=3, external_limit=0)

    assert cut == 3
    assert all(item.source_type == "publication" for item in ordered)


def test_a_page_the_web_holds_stays_evidence_of_the_run_with_a_ref_and_a_lead() -> None:
    outside = web(web_answer())

    result = run([people(), outside], DOSSIER_QUESTION)

    (item,) = page_items(result)
    assert item.source_id == "web:tavily" and item.source_type == "external"
    assert item.fact is None and item.leads == ("charges",)
    assert item.ref.startswith("E") and item.ref in {held.ref for held in result.evidence}
    assert item.url == PAGE and item.text == PUBLISHED
    # A lead is not a fact: the article is still what the pipeline has not decided.
    assert [finding.fact for finding in result.findings] == [
        "role",
        "politics",
        "rf_list",
        "latest_news",
    ]
    assert "статьи обвинения" in result.missing_information


def test_a_page_that_does_not_name_the_person_is_not_her_evidence() -> None:
    outside = web(web_answer("В Тюмени задержали 17-летнего жителя города."))

    result = run([people(), outside], DOSSIER_QUESTION)

    assert page_items(result) == []


def test_a_page_the_local_search_found_too_stands_once_and_keeps_the_leads() -> None:
    outside = web(web_answer())

    result = run([people(), fts(lambda query: [stored_publication()]), outside], DOSSIER_QUESTION)

    (item,) = page_items(result)
    # One document, ranked by every list it stood in, shown as the publication's quote.
    assert item.source_id == "publications_fts" and item.article_id == 7
    assert item.leads == ("charges",)


def test_a_channel_post_stays_one_quote_with_the_publication_the_database_holds() -> None:
    """The same post has two addresses: the database stores the embed view it read
    (`?embed=1&mode=tme`), the channel's own search answers with the bare one. They are
    one post, so the run shows it once and the facts it was looked up for stand on the
    publication — as they did before the web, and as a channel's quotes always do."""
    stored = stored_publication(
        PUBLISHED, url="https://t.me/ovdinfolive/5?embed=1&mode=tme", article_id=11
    )
    channel = SourceSearchRetriever(
        FakeSearch(
            "tg-ovdinfolive",
            lambda query: [
                ExternalEvidence(
                    source="tg-ovdinfolive",
                    source_name="ОВД-Инфо LIVE",
                    external_id="5",
                    url="https://t.me/ovdinfolive/5",
                    text=PUBLISHED,
                    published_at=datetime(2026, 9, 25, tzinfo=UTC),
                    title="Суд над адвокатом",
                )
            ],
        ),
        source="telegram",
    )

    result = run([people_with(stored), channel], DOSSIER_QUESTION)

    posts = [item for item in result.evidence if item.url and "t.me/ovdinfolive/5" in item.url]
    assert len(posts) == 1
    # The publication is what stands, and the fact the post was looked up for is its own.
    (shown,) = posts
    assert shown.article_id == 11
    assert shown.leads == ("charges",)


def test_the_page_the_web_brought_outranks_the_one_only_the_database_found() -> None:
    """Both lists a page stood in rank it: the page the web named as well as the local
    search is the publication, and both its lists count for it."""
    found_in_both = stored_publication(url="https://news.example.test/both", article_id=7)
    found_locally = stored_publication(
        text="Мария Бонцлер идёт по другому делу.",
        url="https://news.example.test/only-local",
        article_id=8,
    )
    outside = web(web_answer(url="https://news.example.test/both"))
    fts_local = fts(lambda query: [found_locally, found_in_both])

    result = run([people(), fts_local, outside], DOSSIER_QUESTION)

    both = [item for item in result.evidence if item.url == "https://news.example.test/both"]
    local_only = [
        item for item in result.evidence if item.url == "https://news.example.test/only-local"
    ]
    assert both and local_only
    # The local search ranked the other page first; the web's own list makes this one
    # the higher of the two.
    assert both[0].ref < local_only[0].ref
    assert both[0].source_id == "publications_fts"


def test_a_page_the_web_brought_and_the_local_search_quoted_for_a_fact_is_not_shown_twice() -> None:
    outside = web(web_answer(published=False))

    result = run(
        [
            people(),
            fts(
                lambda query: (
                    [
                        stored_publication().model_copy(
                            update={"leads": (query.fact,) if query.fact else ()}
                        )
                    ]
                    if query.fact
                    else []
                )
            ),
            outside,
        ],
        DOSSIER_QUESTION,
    )

    (item,) = page_items(result)
    assert item.source_id == "publications_fts"
    # The web answered the first round only: what it brought is the page the local
    # search then quoted, not a second quote of it beside that one.
    assert item.leads == ("charges",)


def test_a_page_the_local_search_did_not_find_stays_the_web_own() -> None:
    outside = web(web_answer(url="https://other.example.test/only-web"))

    result = run([people(), fts(), outside], DOSSIER_QUESTION)

    (item,) = page_items(result, "https://other.example.test/only-web")
    assert item.source_id == "web:tavily" and item.leads == ("charges",)


# -- the figurant's own scenario -----------------------------------------------------------------

CLUE = EvidenceItem(
    source_type="unnamed",
    source_id="unnamed_figurants",
    article_id=28191,
    entity_key="figurant:fig-1",
    url=PAGE,
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
NAMED_ELSEWHERE = EvidenceItem(
    source_type="entity_group",
    source_id="unnamed_figurants",
    article_id=28191,
    entity_key="entity:other",
    title="Петров Пётр",
    text="Петров Пётр назван в публикации 28191",
    fact="named_in_publication",
    value="158.1",
)
STORED_ELSEWHERE = EvidenceItem(
    source_type="publication",
    source_id="publications_fts",
    article_id=30012,
    url="https://other.example.test/stored",
    span_start=0,
    span_end=31,
    text="Иванов из Тюмени: то же дело.",
    published_at=datetime(2026, 9, 21, tzinfo=UTC),
)
FIGURANT_TEXT = "Иванов из Тюмени: то же дело."


def figurants(answers: Answers | None = None) -> FakeRetriever:
    def by_kind(query: InvestigationQuery) -> list[EvidenceItem]:
        if query.kind == "unnamed":
            return [CLUE, CANDIDATE]
        if query.kind == "publication_people":
            return [NAMED_ELSEWHERE]
        return []

    return FakeRetriever(
        "unnamed_figurants",
        answers or by_kind,
        kinds=("unnamed", "publication_people"),
        facts=frozenset({"clues", "candidates"}),
    )


def test_the_keywords_of_an_unnamed_figurant_reach_the_web_as_they_reach_the_channels() -> None:
    outside, chan = web(web_answer(FIGURANT_TEXT, url=OTHER_PAGE)), channel()

    run([figurants(), chan, outside], FIGURANT_QUESTION)

    asked = [call.text for call in search_of(outside).calls]
    assert asked == [call.text for call in search_of(chan).calls]
    # A candidate's surname with the place, and the place with the article.
    assert any("Иванов" in call for call in asked)
    assert any("Тюмень" in call for call in asked)


def test_the_window_of_dates_of_a_figurants_query_reaches_the_web() -> None:
    """«The place and the article, about when the publication came out»: the web is asked
    within the same days the database is, and a query with no window is asked with none."""
    outside = web(web_answer(FIGURANT_TEXT, url=OTHER_PAGE))

    run([figurants(), outside], FIGURANT_QUESTION)

    calls = {call.text: call for call in search_of(outside).calls}
    assert CLUE.published_at is not None
    around = timedelta(days=WINDOW_DAYS)
    bounded = calls["Тюмень 158.1"]
    assert (bounded.published_from, bounded.published_to) == (
        CLUE.published_at - around,
        CLUE.published_at + around,
    )
    by_surname = calls["Иванов Тюмень"]
    assert (by_surname.published_from, by_surname.published_to) == (None, None)


def test_a_text_with_the_place_and_not_the_article_is_no_lead_of_the_dated_query() -> None:
    """The dated query looks for the article in the place. A text of the place alone is
    kept (the place is whom the query is about) and is nobody's corroboration."""
    outside = web(
        lambda query: (
            web_answer("В Тюмени открыли новый мост.", url=OTHER_PAGE)(query)
            if query.text.startswith("Тюмень")
            else []
        )
    )

    result = run([figurants(), outside], FIGURANT_QUESTION)

    (item,) = [held for held in result.evidence if held.source_id == "web:tavily"]
    assert item.leads == ()


def test_the_publication_the_clues_were_read_of_is_not_what_the_web_result_becomes() -> None:
    """The clues carry the address of the publication they were read of. That address is
    not a quote of it: the web's result of that very page stays its own evidence, and a
    fact is never left holding the leads of a page it only describes."""
    outside = web(web_answer(FIGURANT_TEXT, url=PAGE))

    result = run([figurants(), outside], FIGURANT_QUESTION)

    (item,) = [held for held in result.evidence if held.source_id == "web:tavily"]
    assert item.url == PAGE and item.leads == ("corroboration",)
    clues = [held for held in result.evidence if held.fact == "clues"]
    assert clues and all(held.leads == () for held in clues)


def test_only_a_second_publication_about_the_case_corroborates_the_candidate() -> None:
    """The publication of the clue cannot confirm itself, however it came back: by its
    own number, or as the same address from the web. And a name with a place is what any
    list of companies prints — a text has to speak of the case's article as well."""

    def answer(query: SourceSearchQuery) -> list[ExternalEvidence]:
        if query.text.startswith("Иванов"):
            return [
                # The very publication, found again by the web: not a second one.
                ExternalEvidence(
                    source="tavily",
                    source_name="news.example.test",
                    external_id=PAGE,
                    url=PAGE,
                    text=FIGURANT_TEXT,
                    published_at=None,
                    title="Задержали жителя Тюмени",
                ),
                # A listing: name and place, but nothing of the case.
                ExternalEvidence(
                    source="tavily",
                    source_name="rusprofile.example",
                    external_id="rusprofile.example/ip/1",
                    url="https://rusprofile.example/ip/1",
                    text="Иванов Иван, 2009 года рождения, Тюмень, ОГРН 1234567890.",
                    published_at=None,
                    title="Иванов Иван — выписка ЕГРЮЛ",
                ),
                # A second publication about the same case.
                ExternalEvidence(
                    source="tavily",
                    source_name="news.example.test",
                    external_id="news.example.test/second",
                    url="https://news.example.test/second",
                    text="Иванов из Тюмени: то же дело по части 4 статьи 158.1.",
                    published_at=None,
                    title="Второе дело о том же",
                ),
            ]
        return []

    outside = web(answer)

    result = run([figurants(), outside], FIGURANT_QUESTION)

    refs = {
        item.url: item.ref
        for item in result.evidence
        if item.source_id == "web:tavily" and "corroboration" in item.leads
    }
    (candidate,) = result.candidates
    cited = {
        ref
        for signal in candidate.signals
        if signal.signal == "named_with_place"
        for ref in signal.evidence
    }
    # Only the second publication: not the source publication the web returned, and not
    # the listing that carries the name and the place and nothing of the case.
    assert cited == {refs["https://news.example.test/second"]}
    assert refs.get("https://rusprofile.example/ip/1") not in cited
    assert refs.get(PAGE) not in cited


def test_a_text_the_web_holds_corroborates_the_candidate_the_publication_left_unnamed() -> None:
    outside = web(web_answer(FIGURANT_TEXT, url=OTHER_PAGE))

    result = run([figurants(), outside], FIGURANT_QUESTION)

    assert [item for item in result.evidence if item.source_id == "web:tavily"]
    assert [candidate.kind for candidate in result.candidates] == ["rf_entry"]


# -- a web that fails or is slow -------------------------------------------------------------------


def test_a_web_that_fails_is_a_warning_and_not_a_failed_run() -> None:
    outside = web(fail=RuntimeError("no credit"))

    result = run([people(), outside], DOSSIER_QUESTION)

    assert result.status == "partial"
    assert any("web:tavily" in warning for warning in result.warnings)
    assert result.usage.failed_searches >= 1
    # The local part of the run answered regardless.
    assert [item for item in result.evidence if item.source_id == "entity_groups"]


def test_a_web_that_runs_out_of_time_is_a_warning_and_the_run_goes_on() -> None:
    outside = web(web_answer(), slow=5.0)

    result = asyncio.run(
        service(
            people(), outside, limits=Limits(search_timeout_seconds=1, timeout_seconds=60)
        ).investigate(DOSSIER_QUESTION)
    )

    assert result.status in {"partial", "completed"}
    assert any("web:tavily" in warning for warning in result.warnings)
    assert [item for item in result.evidence if item.source_id == "entity_groups"]


# -- the ceilings and a stored run -----------------------------------------------------------------


def test_the_ceiling_on_searches_cuts_the_web_as_it_cuts_any_retriever() -> None:
    outside = web(web_answer())

    result = asyncio.run(
        service(people(), outside, limits=Limits(max_searches=1, max_followups=2)).investigate(
            DOSSIER_QUESTION
        )
    )

    assert result.usage.search_calls == 1
    assert len(search_of(outside).calls) <= 1
    assert result.status == "partial"


def test_a_finished_run_is_read_again_without_asking_the_web_again() -> None:
    store, outside = InMemoryRunStore(), web(web_answer())

    result = asyncio.run(service(people(), outside, runs=store).investigate(DOSSIER_QUESTION))
    asked = [call.text for call in search_of(outside).calls]

    again = asyncio.run(service(people(), outside, runs=store).resume(result.run_id or ""))

    assert asked
    assert [call.text for call in search_of(outside).calls] == asked
    assert again.evidence == result.evidence


def test_a_stored_run_keeps_neither_the_web_key_nor_the_words_it_was_asked() -> None:
    store = InMemoryRunStore()
    with served_tavily_mcp([], expected_key="tvly-secret-key") as (endpoint, recorder):
        outside = SourceSearchRetriever(
            TavilyMcpSearch("tvly-secret-key", url=endpoint, base_delay_seconds=0), source="web"
        )

        result = asyncio.run(service(people(), outside, runs=store).investigate(DOSSIER_QUESTION))

    stored = store.load(result.run_id or "")
    assert stored is not None and recorder.authorizations
    kept = str(stored.progress) + str(stored.result)
    assert "tvly-secret-key" not in kept
    assert "mcp.tavily.com" not in kept
