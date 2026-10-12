"""An investigation over PostgreSQL: the dossier queries of `results`, the two local
retrievers, the command and the endpoint. No model: the rules alone answer.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from functools import cached_property

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.orm import Session, sessionmaker
from support.db_fixtures import DatabaseSeeder

from application import build_investigation_service, build_results_service
from cli.app import main
from cli.context import CliContext
from cli.investigation import _lines as investigation_lines
from db.orm_models import (
    EntityGroupChargeRecord,
    EntityGroupPoliticsRecord,
    EntityGroupRecord,
    EntityGroupRoleRecord,
    EntityMentionRecord,
    ExtractedEventRecord,
    ParsedArticleRecord,
)
from investigation.models import InvestigationResult
from investigation.services.coverage import NOT_FOUND
from processing.identity.service import IdentityService
from results.dossier import QUOTE_CONTEXT
from web.app import app
from web.dependencies import get_db

ARREST = "Суд арестовал Ивана Сидорова по статье 280 УК за антивоенный пост."
OTHER = "Суд арестовал Петра Кузнецова."
# Stored, with no criminal event: no entity group is made of it.
EXHIBITION = "Иван Сидоров открыл выставку в Туле."


@pytest.fixture(autouse=True)
def _no_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)


def _seed(session_factory: sessionmaker[Session], *, verdicts: bool = True) -> None:
    with session_factory() as session:
        seed = DatabaseSeeder(session)
        source = seed.source("news", "https://news.example.test")
        for external_id, body, first, last in (
            ("arrest", ARREST, "Иван", "Сидоров"),
            ("other", OTHER, "Пётр", "Кузнецов"),
        ):
            _, run = seed.article(source, external_id=external_id, title=external_id, text=body)
            surface = body.split("арестовал ")[1].split(" по ")[0].rstrip(".")
            mention_id = seed.mention(run, surface, person_id=None)
            session.get_one(EntityMentionRecord, mention_id).normalized_data = {
                "first_name": first,
                "last_name": last,
                "patronymic": None,
            }
            seed.event(run, "арестовал", event_type="arrest", event_date=None, links=[])
        seed.article(source, external_id="exhibition", title="exhibition", text=EXHIBITION)
        session.commit()
    IdentityService().rebuild_groups(session_factory)
    if not verdicts:
        return
    with session_factory.begin() as session:
        group = session.scalars(
            select(EntityGroupRecord).where(EntityGroupRecord.name == "Иван Сидоров")
        ).one()
        event_id = session.scalars(select(ExtractedEventRecord.id)).first()
        article_id = session.scalars(
            select(ParsedArticleRecord.id).where(ParsedArticleRecord.title == "arrest")
        ).one()
        assert event_id is not None
        session.add_all(
            [
                EntityGroupRoleRecord(
                    group_id=group.id,
                    role="figurant",
                    kind=None,
                    method="rules",
                    reason="арест",
                    quote="Суд арестовал Ивана Сидорова",
                ),
                EntityGroupPoliticsRecord(
                    group_id=group.id,
                    verdict="criminal",
                    method="rules",
                    reason="общеуголовная статья",
                    quote="",
                ),
                EntityGroupChargeRecord(
                    group_id=group.id,
                    event_id=event_id,
                    publication_id=article_id,
                    article="280",
                    event_type="arrest",
                    other_targets=0,
                    quote="по статье 280 УК",
                ),
            ]
        )


def test_a_declined_name_finds_its_person_and_nobody_else(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)
    results = build_results_service(session_factory)

    assert [hit.name for hit in results.find_entities("Ивана Сидорова")] == ["Иван Сидоров"]
    assert [hit.exact for hit in results.find_entities("Иван Сидоров")] == [True]
    assert [hit.exact for hit in results.find_entities("Ивана Сидорова")] == [False]
    # «Сидор» begins «Сидоров», but «идоров» begins no word of the name.
    assert results.find_entities("идоров") == []
    assert results.find_entities("Алексей Сидоров") == []
    assert results.find_entities("?!") == []


def test_a_person_is_found_behind_any_number_of_names_that_only_hold_the_letters(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        for number in range(8):
            # «Ливанов» holds «иван» inside a word and is mentioned more than Ivan is.
            session.add(
                EntityGroupRecord(
                    key=f"пётр ливанов {number}",
                    name=f"Пётр Ливанов{'а' * number}",
                    variants=[],
                    mention_count=100 - number,
                    article_count=1,
                    event_types={},
                )
            )
        session.add(
            EntityGroupRecord(
                key="иван кузьмин",
                name="Иван Кузьмин",
                variants=[],
                mention_count=1,
                article_count=1,
                event_types={},
            )
        )

    hits = build_results_service(session_factory).find_entities("Иван", limit=3)

    assert [hit.name for hit in hits] == ["Иван Кузьмин"]


def test_what_the_pipeline_decided_is_read_with_the_words_it_rests_on(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)
    results = build_results_service(session_factory)
    (hit,) = results.find_entities("Сидоров")

    facts = {stated.fact: stated for stated in results.entity_facts(hit.key) or []}

    assert {name: stated.value for name, stated in facts.items()} == {
        "role": "figurant",
        "charges": "280",
        "politics": "criminal",
    }
    assert facts["role"].basis == "Суд арестовал Ивана Сидорова"
    # A verdict kept with no quote rests on its reason.
    assert facts["politics"].basis == "общеуголовная статья"
    assert results.entity_facts("никто никтов") is None


def test_a_quote_is_the_articles_own_text_at_its_offsets(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)
    results = build_results_service(session_factory)
    (hit,) = results.find_entities("Сидоров")

    (quote,) = results.entity_evidence(hit.key)

    assert ARREST[quote.span_start : quote.span_end] == quote.quote
    assert "Ивана Сидорова" in quote.quote
    (term,) = results.entity_publication_quotes(hit.key, ["статье"])
    assert ARREST[term.span_start : term.span_end] == term.quote and "статье 280" in term.quote
    assert results.entity_publication_quotes(hit.key, ["приговор"]) == []
    assert results.entity_publication_quotes(hit.key, [" "]) == []


def test_a_quote_cut_out_of_a_long_text_keeps_the_texts_own_offsets(
    session_factory: sessionmaker[Session],
) -> None:
    body = "а" * 500 + " Затем выступил Пётр Ёжиков из Тулы. " + "б" * 500
    with session_factory() as session:
        seed = DatabaseSeeder(session)
        seed.article(
            seed.source("news", "https://news.example.test"),
            external_id="long",
            title="long",
            text=body,
        )
        session.commit()

    (quote,) = build_results_service(session_factory).stored_publication_quotes("Петр Ежиков")

    assert body[quote.span_start : quote.span_end] == quote.quote
    assert quote.span_end - quote.span_start == 2 * QUOTE_CONTEXT
    assert 0 < quote.span_start and quote.span_end < len(body)
    assert quote.quote.index("Ёжиков") == QUOTE_CONTEXT


def test_stored_publications_are_searched_by_their_text(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)
    results = build_results_service(session_factory)

    quotes = results.stored_publication_quotes("Иван Сидоров")

    assert sorted(quote.title for quote in quotes) == ["arrest", "exhibition"]
    assert all("Сидоров" in quote.quote for quote in quotes)
    assert results.stored_publication_quotes("Кузнецов Иван") == []


def test_an_investigation_reports_the_facts_with_their_evidence_and_the_gap(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)
    service = build_investigation_service(session_factory, env={})

    result = service.investigate_sync("Что известно про Ивана Сидорова?")

    assert result.status == "partial"
    assert result.subject is not None and result.subject.entity_key == "иван сидоров"
    by_ref = {item.ref: item for item in result.evidence}
    assert {finding.fact: finding.statement for finding in result.findings} == {
        "role": "роль в деле: figurant",
        "charges": "статьи обвинения: 280",
        "politics": "политическое ли преследование: criminal",
    }
    for finding in result.findings:
        assert finding.evidence and set(finding.evidence) <= set(by_ref)
        assert all(by_ref[ref].entity_key == "иван сидоров" for ref in finding.evidence)
    # The list was never checked: said as missing, not as «not on the list».
    assert result.missing_information == ["есть ли в перечне Росфинмониторинга"]
    # Both local sources answered; the exhibition comes from the stored texts only.
    assert {item.source_id for item in result.evidence} == {
        "entity_groups",
        "stored_publications",
    }
    urls = {item.url for item in result.evidence if item.source_type == "publication"}
    assert len(urls) == 2 and all(url for url in urls)
    assert (result.usage.search_calls, result.usage.llm_calls) == (2, 0)


def test_missing_facts_are_followed_up_in_the_persons_own_publications(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory, verdicts=False)
    service = build_investigation_service(session_factory, env={})

    result = service.investigate_sync("Иван Сидоров")

    assert result.status == "partial" and result.findings == []
    assert result.usage.followup_rounds >= 1
    leads = {lead for item in result.evidence for lead in item.leads}
    assert "charges" in leads
    lead = next(item for item in result.evidence if "charges" in item.leads)
    assert "статье 280" in lead.text and lead.article_id is not None


def test_a_stranger_is_not_found_and_nothing_is_made_up(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)
    service = build_investigation_service(session_factory, env={})

    result = service.investigate_sync("Найди Алексея Неизвестного")

    assert (result.status, result.missing_information) == ("partial", [NOT_FOUND])
    assert result.findings == [] and result.evidence == []


def test_nothing_is_written_by_an_investigation(session_factory: sessionmaker[Session]) -> None:
    _seed(session_factory)

    def counts() -> dict[str, int]:
        with session_factory() as session:
            tables = ("entity_groups", "entity_group_roles", "parsed_articles", "persons")
            return {
                table: session.connection()
                .exec_driver_sql(f"SELECT count(*) FROM {table}")
                .scalar_one()
                for table in tables
            }

    before = counts()
    build_investigation_service(session_factory, env={}).investigate_sync("Иван Сидоров")

    assert counts() == before
    assert before["persons"] == 0


class _Context(CliContext):
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._factory = session_factory

    @cached_property
    def session_factory(self) -> sessionmaker[Session]:
        return self._factory


def test_the_command_prints_the_result_as_json(
    session_factory: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(session_factory)

    main(
        ["investigate", "Иван Сидоров", "--json", "--max-followups", "0", "--local-only"],
        context=_Context(session_factory),
    )

    result = InvestigationResult.model_validate(json.loads(capsys.readouterr().out))
    assert result.status == "partial" and result.usage.followup_rounds == 0
    assert len(result.findings) == 3


def test_the_command_prints_what_a_person_reads(
    session_factory: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(session_factory)

    main(["investigate", "Иван Сидоров", "--no-web"], context=_Context(session_factory))

    printed = capsys.readouterr().out
    assert "Статус: partial" in printed and "роль в деле: figurant [E" in printed
    assert "Не хватает:" in printed and "Материалы:" in printed


def test_the_command_prints_a_models_summary_under_its_warning() -> None:
    result = InvestigationResult(
        status="completed", question="Иван Сидоров", model_summary="Сидоров — фигурант. [E1]"
    )

    lines = investigation_lines(result)

    heading = lines.index("Сводка модели (не проверена):")
    assert lines[heading + 1] == "Сидоров — фигурант. [E1]"
    assert "Сводка модели (не проверена):" not in investigation_lines(
        result.model_copy(update={"model_summary": None})
    )


def test_the_search_for_a_name_loads_no_more_groups_than_it_needs(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        session.add_all(
            EntityGroupRecord(
                key=f"иван человек{number}",
                name=f"Иван Человек{'ов' * number}",
                variants=[],
                mention_count=1000 - number,
                article_count=1,
                event_types={},
            )
            for number in range(1, 400)
        )
    statements: list[str] = []
    engine = session_factory.kw["bind"]

    def record(conn: object, cursor: object, statement: str, *rest: object) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        hits = build_results_service(session_factory).find_entities("Иван", limit=5)
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert len(hits) == 5
    (query,) = [statement for statement in statements if "entity_groups" in statement]
    # A ceiling in SQL, and only the columns a hit is made of: no JSON of a group.
    assert "LIMIT" in query.upper()
    assert "variants" not in query and "event_types" not in query


def test_a_name_after_a_hyphen_or_a_quote_is_a_word_of_its_own(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        for name in ("Анна Петрова-Водкина", "Шон О'Коннор", "Пётр Подводкин"):
            session.add(
                EntityGroupRecord(
                    key=name.lower(),
                    name=name,
                    variants=[],
                    mention_count=1,
                    article_count=1,
                    event_types={},
                )
            )
    results = build_results_service(session_factory)

    assert [hit.name for hit in results.find_entities("Водкина")] == ["Анна Петрова-Водкина"]
    assert [hit.name for hit in results.find_entities("Коннор")] == ["Шон О'Коннор"]


def test_the_command_refuses_a_negative_ceiling(session_factory: sessionmaker[Session]) -> None:
    with pytest.raises(SystemExit, match="must not be negative"):
        main(
            ["investigate", "Иван Сидоров", "--max-followups", "-1"],
            context=_Context(session_factory),
        )


@contextmanager
def _client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    def override() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_the_endpoint_answers_with_the_typed_result(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)

    with _client(session_factory) as client:
        response = client.post(
            "/api/v1/investigation", json={"query": "Что известно про Ивана Сидорова?"}
        )

    assert response.status_code == 200
    result = InvestigationResult.model_validate(response.json())
    assert result.status == "partial" and len(result.findings) == 3
    assert result.subject is not None and result.subject.entity_key == "иван сидоров"


def test_the_endpoint_refuses_an_empty_question_and_another_origin(
    session_factory: sessionmaker[Session],
) -> None:
    with _client(session_factory) as client:
        empty = client.post("/api/v1/investigation", json={"query": ""})
        foreign = client.post(
            "/api/v1/investigation",
            json={"query": "Иван Сидоров"},
            headers={"Origin": "https://evil.example"},
        )

    assert empty.status_code == 422
    assert foreign.status_code == 403
