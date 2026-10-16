"""«Модель извлечения»: the choice kept in the database, «Тест», the warning before a
start, the run fixed to the model it was started with, and the ledger of what the model
spent. The model is never asked: a test's call is a fake."""

from __future__ import annotations

import argparse
import dataclasses
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker
from support.openrouter_catalog import CATALOG_PAYLOAD

from cli import cycle as cli_cycle
from db.locks import try_advisory_lock
from db.models.entities import EntityGroupRecord
from db.models.settings import EntityModelSpendRecord
from llm import model_choice
from llm.chat import LEDGER, ModelTally
from llm.model_choice import CatalogCache, book_ledger, spend_summary, stored_model
from orchestrator.operations import OperationParameters, OperationRegistry
from processing.identity import model_probe
from processing.identity.model_probe import FoundEntity, ProbeResult
from processing.identity.normalizer import OpenRouterNameNormalizer, name_normalizer_from_env
from web import entity_model
from web.app import app
from web.dependencies import get_db, get_operation_registry
from web.ui import spend
from web.ui.pipeline import step_confirmation

FREE = "nvidia/nemotron-3.5-super-vl:free"
QWEN = "qwen/qwen3.8-flash"
DEEPSEEK = "deepseek/deepseek-v4.1-flash"


@pytest.fixture(autouse=True)
def _openrouter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("ENTITY_NORMALIZE_MODEL", raising=False)
    monkeypatch.setenv("JUNK_SCREEN", "jev")


@contextmanager
def _client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    def override() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    registry = OperationRegistry(session_factory, executor=lambda _work: None)
    app.dependency_overrides[get_db] = override
    app.dependency_overrides[get_operation_registry] = lambda: registry
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_operation_registry, None)


def _pages_read(monkeypatch: pytest.MonkeyPatch, session_factory: sessionmaker[Session]) -> None:
    """The warnings of the pages read the test database's choice."""

    def read() -> str | None:
        with session_factory() as session:
            return stored_model(session)

    monkeypatch.setattr(entity_model, "read_stored", read)
    entity_model.forget()


def _passing(model: str, _key: str) -> ProbeResult:
    return ProbeResult(
        ok=True,
        model=model,
        seconds=0.8,
        prompt_tokens=812,
        completion_tokens=96,
        cost_usd=0.0,
        entities=[FoundEntity("Александра Моора", "Александр Моор", "male", True, True)],
        expected_matched=1,
    )


def _failing(model: str, _key: str) -> ProbeResult:
    return ProbeResult(
        ok=False, model=model, seconds=60.0, error="Модель не ответила за 60 с (timeout)."
    )


def test_an_old_installation_keeps_deepseek_and_sees_ten_models_with_prices(
    session_factory: sessionmaker[Session],
) -> None:
    with _client(session_factory) as client:
        settings = client.get("/api/v1/entity-model").json()

    assert settings["current"] == DEEPSEEK == settings["default"]
    assert settings["source"] == "environment"
    assert len(settings["models"]) == 10
    by_id = {model["id"]: model for model in settings["models"]}
    assert by_id[DEEPSEEK]["prompt_usd_per_million"] == 0.09
    assert by_id[DEEPSEEK]["completion_usd_per_million"] == 0.18
    assert by_id[FREE]["free"] and by_id[FREE]["structured_outputs"] and by_id[FREE]["usable"]
    assert not by_id["stealth/space-bunny-alpha"]["usable"], "not in the catalog any more"
    assert "test-key" not in json.dumps(settings), "the key never reaches the browser"


def test_a_model_is_saved_only_after_it_passed_the_test_and_survives_a_restart(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    with _client(session_factory) as client:
        untested = client.post("/api/v1/entity-model", json={"model": FREE})
        monkeypatch.setattr(model_probe, "probe", _failing)
        failed = client.post("/api/v1/entity-model/test", json={"model": FREE}).json()
        still_untested = client.post("/api/v1/entity-model", json={"model": FREE})
        monkeypatch.setattr(model_probe, "probe", _passing)
        passed = client.post("/api/v1/entity-model/test", json={"model": FREE}).json()
        saved = client.post("/api/v1/entity-model", json={"model": FREE})
        unknown = client.post("/api/v1/entity-model", json={"model": "openai/gpt-5"})
        absent = client.post("/api/v1/entity-model", json={"model": "stealth/space-bunny-alpha"})

    assert untested.status_code == 400 and "Тест" in untested.json()["detail"]
    assert not failed["ok"] and "timeout" in failed["error"]
    assert still_untested.status_code == 400
    assert passed["ok"] and passed["entities"][0]["nominative"] == "Александр Моор"
    assert passed["expected_total"] == 3
    assert saved.status_code == 200 and saved.json()["current"] == FREE
    assert saved.json()["source"] == "stored"
    assert unknown.status_code == 400 and absent.status_code == 400
    # A new process reads it from the database, no environment needed.
    with session_factory() as session:
        assert model_choice.chosen_model(session, {}) == FREE


def test_the_test_writes_no_entity_and_books_its_cost_apart(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(model_probe, "probe", _passing)

    with _client(session_factory) as client:
        client.post("/api/v1/entity-model/test", json={"model": QWEN})

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(EntityGroupRecord)) == 0
        [row] = session.scalars(select(EntityModelSpendRecord)).all()
    assert (row.command, row.model, row.prompt_tokens, row.completion_tokens) == (
        "model-test",
        QWEN,
        812,
        96,
    )


def test_a_second_test_while_one_runs_is_refused(
    session_factory: sessionmaker[Session], test_engine: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[str] = []
    monkeypatch.setattr(
        model_probe, "probe", lambda model, key: asked.append(model) or _passing(model, key)
    )

    with (
        _client(session_factory) as client,
        try_advisory_lock(test_engine, model_probe.LOCK_KEY) as held,
    ):  # type: ignore[arg-type]
        assert held
        busy = client.post("/api/v1/entity-model/test", json={"model": FREE})

    assert busy.status_code == 409 and asked == [], "no call while another test runs"


def test_without_openrouter_there_is_no_test(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY")

    with _client(session_factory) as client:
        refused = client.post("/api/v1/entity-model/test", json={"model": FREE})

    assert refused.status_code == 503


def _choose(session_factory: sessionmaker[Session], model: str) -> None:
    with session_factory.begin() as session:
        model_choice.mark_tested(session, model)
        model_choice.save_choice(
            session, model, CatalogCache(request=lambda: CATALOG_PAYLOAD).get()
        )


def test_a_run_is_started_with_the_chosen_model_and_keeps_it(
    session_factory: sessionmaker[Session],
) -> None:
    _choose(session_factory, QWEN)
    registry = OperationRegistry(session_factory, executor=lambda _work: None)

    run = registry.start("monitor", OperationParameters(mode="figurants"))
    # A choice saved while it runs changes nothing of it.
    _choose(session_factory, FREE)
    again = registry.get(run.id)

    assert again.parameters.entity_model == QWEN
    assert again.command[-3:] == ["find-figurants", "--entity-model", QWEN]


def test_the_purge_is_not_given_the_entity_model(session_factory: sessionmaker[Session]) -> None:
    """Jev keeps its own settings: the choice is the entity steps' alone."""
    _choose(session_factory, FREE)
    registry = OperationRegistry(session_factory, executor=lambda _work: None)

    run = registry.start("monitor", OperationParameters(mode="purge"))

    assert run.command[-1] == "purge-junk" and "--entity-model" not in run.command
    assert run.parameters.entity_model is None
    assert "JEV" in step_confirmation("purge")


def test_a_chain_carries_its_model_to_the_next_step(session_factory: sessionmaker[Session]) -> None:
    _choose(session_factory, QWEN)
    registry = OperationRegistry(session_factory, executor=lambda _work: None)
    first = registry.start("monitor", OperationParameters(mode="entities", chain=True))
    _choose(session_factory, FREE)
    with session_factory.begin() as session:
        session.execute(text("UPDATE operator_operation_runs SET status = 'succeeded'"))

    registry._continue_chain(first.id)

    following = registry.list_runs()[0]
    assert following.parameters.mode == "figurants"
    assert following.parameters.entity_model == QWEN
    assert following.command[-2:] == ["--entity-model", QWEN]


def test_do_all_shows_the_model_and_its_price_and_starts_with_it(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    _choose(session_factory, QWEN)
    _pages_read(monkeypatch, session_factory)

    with _client(session_factory) as client:
        cycle = client.get("/api/v1/cycle").json()
        _choose(session_factory, FREE)
        stale = client.post("/api/v1/cycle/start", json={"after": 0, "entity_model": QWEN})
        started = client.post("/api/v1/cycle/start", json={"after": 0, "entity_model": FREE})
        run = client.get(f"/api/v1/operations/runs/{started.json()['run_id']}").json()

    notice = cycle["entity_model"]
    assert notice["model"] == QWEN and not notice["free"]
    assert (notice["prompt_usd_per_million"], notice["completion_usd_per_million"]) == (0.15, 0.47)
    assert notice["kind"] == "платная"
    assert stale.status_code == 409 and FREE in stale.json()["detail"]
    assert started.status_code == 200
    assert run["parameters"]["entity_model"] == FREE


def test_the_warning_says_free_for_a_free_model_and_unknown_for_an_unknown_price(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    _choose(session_factory, FREE)
    _pages_read(monkeypatch, session_factory)

    free = step_confirmation("entities")
    assert FREE in free and "FREE" in free and "Шаг бесплатный" in free
    assert not spend.paid("entities")

    down = CatalogCache(request=lambda: (_ for _ in ()).throw(httpx.ConnectError("down")))
    monkeypatch.setattr(spend, "CATALOG", down)
    _choose(session_factory, DEEPSEEK)
    entity_model.forget()

    unknown = step_confirmation("figurants")
    assert DEEPSEEK in unknown and "Цена сейчас неизвестна" in unknown
    assert "$0" not in unknown, "an unknown price is never shown as zero"
    assert spend.paid("figurants")


@dataclasses.dataclass
class _Found:
    asked_model: str


class _Cycle:
    def figurants(self) -> _Found:
        normalizer = name_normalizer_from_env()
        assert isinstance(normalizer, OpenRouterNameNormalizer)
        LEDGER.add(normalizer.model, 0.0021, prompt_tokens=1500, completion_tokens=300)
        LEDGER.add(normalizer.model, None, prompt_tokens=1000, completion_tokens=100)
        return _Found(asked_model=normalizer.model)


def test_a_scheduled_step_asks_the_stored_choice_and_books_what_it_spent(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Started without `--entity-model` (a schedule, a shell): the stored choice, read once,
    reaches the production extractor; the step's spend is booked per model."""
    _choose(session_factory, QWEN)
    monkeypatch.setattr(cli_cycle, "configure_logging", lambda: None)
    # The command sets the variable for its process: undone after the test.
    monkeypatch.setenv("ENTITY_NORMALIZE_MODEL", "from-the-environment")
    args = argparse.Namespace(command="find-figurants", entity_model=None)

    assert cli_cycle.run_cycle_command(args, session_factory, build_cycle=lambda _f: _Cycle())  # type: ignore[arg-type,return-value]

    printed = json.loads(capsys.readouterr().out)
    assert printed == {"asked_model": QWEN, "entity_model": QWEN}
    with session_factory() as session:
        [row] = session.scalars(select(EntityModelSpendRecord)).all()
        summary = spend_summary(session, CatalogCache(request=lambda: CATALOG_PAYLOAD).get())
    assert (row.command, row.model, row.calls, row.uncosted_calls) == ("find-figurants", QWEN, 2, 1)
    [today] = summary.today
    assert today.cost_usd == 0.0021
    # The call without a reported cost: by the price list, an estimate.
    assert today.estimated_usd == round((1000 * 0.15 + 100 * 0.47) / 1_000_000, 6)
    assert not LEDGER.active


def test_spend_is_summed_for_today_and_for_all_time(session_factory: sessionmaker[Session]) -> None:
    book_ledger(session_factory, "collect-entities", {DEEPSEEK: ModelTally(3, 3000, 600, 0.0005)})
    with session_factory.begin() as session:
        old = EntityModelSpendRecord(
            command="find-political",
            model=DEEPSEEK,
            calls=1,
            prompt_tokens=100,
            completion_tokens=10,
            cost_usd=0.0001,
            created_at=datetime.now(UTC) - timedelta(days=3),
        )
        session.add(old)

    with session_factory() as session:
        summary = spend_summary(session, CatalogCache(request=lambda: CATALOG_PAYLOAD).get())

    assert [(line.calls, line.cost_usd) for line in summary.today] == [(3, 0.0005)]
    assert [(line.calls, line.cost_usd) for line in summary.total] == [(4, 0.0006)]


def test_a_live_run_card_names_the_model_the_run_asks_not_a_later_choice(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    _choose(session_factory, QWEN)
    _pages_read(monkeypatch, session_factory)
    registry = OperationRegistry(session_factory, executor=lambda _work: None)
    run = registry.start("monitor", OperationParameters(mode="figurants"))
    with session_factory.begin() as session:
        session.execute(
            text(
                "UPDATE operator_operation_runs SET status = 'running', started_at = now(), "
                "heartbeat_at = now() WHERE id = :id"
            ),
            {"id": run.id},
        )
    _choose(session_factory, FREE)
    entity_model.forget()

    with _client(session_factory) as client:
        page = client.get(f"/ui/runs?run_id={run.id}").text

    card = page.split('<section class="band run-card">', 1)[1].split("</section>", 1)[0]
    assert QWEN in card and FREE not in card
