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

import httpx
import pytest
from sqlalchemy.orm import Session, sessionmaker

from application import build_investigation_retrievers
from ingestion.search import ExternalEvidence, SourceSearchQuery
from ingestion.web_search import TAVILY_URL, TavilySearch
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
PUBLISHED = "Мария Бонцлер арестована по статье 280 УК РФ."


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
    assert isinstance(search, TavilySearch)
    # The web waits no longer than any other search of the run.
    assert search.timeout.read == 7


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


def test_a_text_with_the_word_in_another_case_is_a_lead_and_ends_the_search_for_it() -> None:
    outside = web(web_answer("Марии Бонцлер вменили две статьи."))

    result = run([people(), outside], DOSSIER_QUESTION, max_followups=2)

    (item,) = page_items(result)
    assert item.leads == ("charges",)
    assert f"{NAME} обвинение" not in [call.text for call in search_of(outside).calls]


def test_a_word_that_only_begins_the_same_way_is_still_the_word_looked_for() -> None:
    """The known tolerance of the rule, kept on purpose: a word is found by the beginning
    of the word looked for, so «статус» answers a search for «статья». It costs the run
    the extra round for that fact and nothing more — a lead is never a fact, and the
    articles stay in what the result says is missing."""
    outside = web(web_answer("Изменился статус Марии Бонцлер."))

    result = run([people(), outside], DOSSIER_QUESTION, max_followups=2)

    (item,) = page_items(result)
    assert item.leads == ("charges",)
    assert f"{NAME} обвинение" not in [call.text for call in search_of(outside).calls]
    assert "статьи обвинения" in result.missing_information
    assert all(finding.fact != "charges" for finding in result.findings)


def test_the_words_travel_to_tavily_in_the_body_and_the_key_in_the_header() -> None:
    asked: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        return httpx.Response(200, json={"results": []})

    outside = SourceSearchRetriever(
        TavilySearch(
            "a-key",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            base_delay_seconds=0,
        ),
        source="web",
    )

    run([people(), outside], DOSSIER_QUESTION)

    assert asked and all(str(request.url) == TAVILY_URL for request in asked)
    assert all(request.headers["Authorization"] == "Bearer a-key" for request in asked)


# -- the dossier of a person ---------------------------------------------------------------------


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
    asked: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        return httpx.Response(200, json={"results": []})

    outside = SourceSearchRetriever(
        TavilySearch(
            "tvly-secret-key",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            base_delay_seconds=0,
        ),
        source="web",
    )

    result = asyncio.run(service(people(), outside, runs=store).investigate(DOSSIER_QUESTION))

    stored = store.load(result.run_id or "")
    assert stored is not None and asked
    kept = str(stored.progress) + str(stored.result)
    assert "tvly-secret-key" not in kept
    assert "api.tavily.com" not in kept
