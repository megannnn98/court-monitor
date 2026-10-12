"""The investigation on a disposable PostgreSQL: ranked text search of the stored
publications, the unnamed figurant end to end, stored runs, and the entry points.

No model and no site: every retriever outside the database is configured off.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker
from support.cli_context import SessionContext as Context
from support.db_fixtures import DatabaseSeeder

from application import build_investigation_service, build_results_service
from cli.app import main
from db.orm_models import (
    RosfinmonitoringEntryRecord,
    RosfinmonitoringSnapshotRecord,
    UnnamedFigurantRecord,
)
from investigation.models import InvestigationQuery, InvestigationResult
from investigation.services.retrieval import LexicalPublicationsRetriever
from web.app import app
from web.dependencies import get_db

LOCAL_ENV = {"INVESTIGATION_TELEGRAM_CHANNELS": "none"}
NEWS = datetime(2024, 12, 10, tzinfo=UTC)

TEXTS = {
    "arrest": "Суд в Туле арестовал Марию Бонцлер по статье 275 УК о госизмене.",
    "appeal": "Защита Бонцлер Марии обжаловала арест; адвокат говорит о давлении.",
    "court": "В суде выступил адвокат. Бонцлер не доставили. Бонцлер ждёт приговора, Бонцлер.",
    "other": "В Москве задержан Иван Петров по делу о краже.",
    "teen": "В Тюмени задержан 17-летний житель города по делу о теракте, статья 205 УК.",
}


@pytest.fixture(autouse=True)
def _no_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)


def _articles(session_factory: sessionmaker[Session]) -> dict[str, int]:
    ids: dict[str, int] = {}
    with session_factory() as session:
        seed = DatabaseSeeder(session)
        source = seed.source("news", "https://news.example.test")
        for key, body in TEXTS.items():
            ids[key], _ = seed.article(
                source, external_id=key, title=key, text=body, published_at=NEWS
            )
        session.commit()
    return ids


# -- ranked text search --------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["Мария Бонцлер", "Марию Бонцлер", "Бонцлер Мария", "М. Бонцлер", "марии бонцлер"],
)
def test_a_name_is_found_declined_reversed_or_with_an_initial(
    session_factory: sessionmaker[Session], name: str
) -> None:
    ids = _articles(session_factory)

    found = build_results_service(session_factory).search_publications(name=name)

    assert ids["arrest"] in {item.article_id for item in found}
    assert ids["other"] not in {item.article_id for item in found}


def test_words_of_a_case_find_their_publication(session_factory: sessionmaker[Session]) -> None:
    ids = _articles(session_factory)
    results = build_results_service(session_factory)

    by_article = results.search_publications(name="Бонцлер", any_of=["госизмена", "статья"])
    by_place = results.search_publications(name="Тула", any_of=["арест"])
    by_unnamed = results.search_publications(name="Тюмень", any_of=["205", "теракт"])

    assert [item.article_id for item in by_article] == [ids["arrest"]]
    assert [item.article_id for item in by_place] == [ids["arrest"]]
    assert [item.article_id for item in by_unnamed] == [ids["teen"]]


def test_a_text_that_says_the_name_more_ranks_higher(
    session_factory: sessionmaker[Session],
) -> None:
    ids = _articles(session_factory)

    found = build_results_service(session_factory).search_publications(name="Бонцлер")

    assert found[0].article_id == ids["court"]
    assert all(found[i].rank >= found[i + 1].rank for i in range(len(found) - 1))
    # The quote is the article's own text at its offsets, around the name.
    first = found[0]
    assert TEXTS["court"][first.span_start : first.span_end] == first.quote
    assert "Бонцлер" in first.quote


def test_a_date_window_keeps_to_its_days(session_factory: sessionmaker[Session]) -> None:
    _articles(session_factory)
    results = build_results_service(session_factory)

    before = results.search_publications(
        name="Бонцлер", published_to=datetime(2024, 1, 1, tzinfo=UTC)
    )

    assert before == []


def test_the_lexical_retriever_returns_ranked_evidence_with_its_provenance(
    session_factory: sessionmaker[Session],
) -> None:
    ids = _articles(session_factory)
    retriever = LexicalPublicationsRetriever(build_results_service(session_factory))

    found = asyncio.run(
        retriever.search(InvestigationQuery(kind="keywords", name="Бонцлер", fact="charges"))
    )

    assert {item.article_id for item in found} == {ids["arrest"], ids["appeal"], ids["court"]}
    assert all(item.score_type == "ts_rank" and item.score is not None for item in found)
    assert all(item.leads == ("charges",) and item.source_name == "news" for item in found)


def test_the_same_publication_found_by_name_and_by_rank_is_one_piece_of_evidence(
    session_factory: sessionmaker[Session],
) -> None:
    _articles(session_factory)
    service = build_investigation_service(session_factory, env=LOCAL_ENV)

    result = service.investigate_sync("Что известно про Марию Бонцлер?")

    # Each article was found by name and by rank; each is held once.
    articles = [item.article_id for item in result.evidence if item.article_id is not None]
    assert articles and len(articles) == len(set(articles))
    assert result.usage.local_searches == 3


# -- the unnamed figurant ----------------------------------------------------------------------


def _unnamed(session_factory: sessionmaker[Session]) -> int:
    ids = _articles(session_factory)
    teen = ids["teen"]
    with session_factory.begin() as session:
        session.add(
            UnnamedFigurantRecord(
                key="t" * 64,
                article_id=teen,
                start_offset=0,
                end_offset=60,
                quote=TEXTS["teen"][:60],
                age=17,
                gender="male",
                place="Тюмень",
                initial=None,
                articles=["205"],
                event_type="detention",
                explanation="теракт",
                published_at=NEWS,
            )
        )
        snapshot = RosfinmonitoringSnapshotRecord(
            snapshot_date=datetime(2024, 12, 1, tzinfo=UTC),
            source_url="https://fedsfm.test",
            content_hash="b",
            entry_count=3,
            fetched_at=datetime(2024, 12, 1, tzinfo=UTC),
        )
        session.add(snapshot)
        session.flush()
        for full_name, born, place in (
            ("ПУРТОВ ЕГОР ВЛАДИМИРОВИЧ", datetime(2007, 2, 17, tzinfo=UTC), "Г. ТЮМЕНЬ"),
            ("СИДОРОВ ПЁТР ИВАНОВИЧ", datetime(2007, 5, 1, tzinfo=UTC), "Г. ОМСК"),
            ("ИВАНОВА АННА ПЕТРОВНА", datetime(2007, 3, 3, tzinfo=UTC), "Г. ТЮМЕНЬ"),
        ):
            session.add(
                RosfinmonitoringEntryRecord(
                    snapshot_id=snapshot.id,
                    full_name=full_name,
                    normalized_name=full_name.lower(),
                    matching_key=full_name.lower().replace(" ", ""),
                    birth_date=born,
                    birth_place=place,
                )
            )
    return teen


def test_an_unnamed_figurant_is_matched_to_who_fits_and_left_to_a_person(
    session_factory: sessionmaker[Session],
) -> None:
    teen = _unnamed(session_factory)
    service = build_investigation_service(session_factory, env=LOCAL_ENV)

    result = service.investigate_sync(f"Установи фигуранта публикации {teen}")

    assert result.scenario == "identify_unnamed"
    names = [candidate.name for candidate in result.candidates]
    # Born in Tyumen, a man of 17: first. The girl is not a «житель»; Omsk is elsewhere.
    assert names[0] == "ПУРТОВ ЕГОР ВЛАДИМИРОВИЧ"
    assert "ИВАНОВА АННА ПЕТРОВНА" not in names
    signals = {signal.signal for signal in result.candidates[0].signals}
    assert {"age_exact", "gender", "birthplace"} <= signals
    clue = next(finding for finding in result.findings if finding.fact == "clues")
    assert "place: Тюмень" in clue.statement and clue.evidence
    assert result.status in ("completed", "review_required")
    assert any("не установленная личность" in warning for warning in result.warnings)


def test_a_publication_with_nobody_unnamed_says_so(session_factory: sessionmaker[Session]) -> None:
    ids = _articles(session_factory)
    service = build_investigation_service(session_factory, env=LOCAL_ENV)

    result = service.investigate_sync(f"Установи фигуранта публикации {ids['other']}")

    assert result.status == "partial" and result.candidates == []


# -- stored runs and the entry points ----------------------------------------------------------


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


def test_a_run_is_stored_and_read_again_by_its_id(session_factory: sessionmaker[Session]) -> None:
    _articles(session_factory)

    with _client(session_factory) as client:
        posted = client.post(
            "/api/v1/investigation",
            json={"query": "Что известно про Марию Бонцлер?", "allowed_sources": ["local"]},
        )
        run_id = posted.json()["run_id"]
        read = client.get(f"/api/v1/investigation/{run_id}")
        missing = client.get("/api/v1/investigation/0123456789abcdef")

    assert posted.status_code == 200 and run_id
    assert read.status_code == 200
    body = read.json()
    assert body["status"] == posted.json()["status"]
    assert InvestigationResult.model_validate(body["result"]) == InvestigationResult.model_validate(
        posted.json()
    )
    assert missing.status_code == 404


def test_an_unknown_source_is_refused_by_the_endpoint_and_the_command(
    session_factory: sessionmaker[Session],
) -> None:
    with _client(session_factory) as client:
        refused = client.post(
            "/api/v1/investigation", json={"query": "Иван Сидоров", "allowed_sources": ["google"]}
        )

    assert refused.status_code == 422 and "google" in refused.json()["detail"]
    with pytest.raises(SystemExit, match="unknown source: google"):
        main(
            ["investigate", "Иван Сидоров", "--source", "google"], context=Context(session_factory)
        )


def test_the_command_reads_a_stored_run_by_its_id(
    session_factory: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    _articles(session_factory)
    main(["investigate", "Мария Бонцлер", "--json", "--no-llm"], context=Context(session_factory))
    first = InvestigationResult.model_validate(json.loads(capsys.readouterr().out))
    assert first.run_id is not None

    main(["investigate", "--run-id", first.run_id, "--json"], context=Context(session_factory))

    assert InvestigationResult.model_validate(json.loads(capsys.readouterr().out)) == first
