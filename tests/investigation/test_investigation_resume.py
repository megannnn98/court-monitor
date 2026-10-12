"""A stored run, stopped and carried on: what it may do when it is resumed, who may work
on it, and what survives a stop in the middle of a round.

Every stop here is a process that died — an exception nothing catches — at a named
point; the store is the in-memory one, whose contract the PostgreSQL store keeps
(`test_investigation_db.py`)."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable, Sequence
from typing import Any, ClassVar

import pytest

from investigation import nodes
from investigation.models import (
    CoverageAssessment,
    EvidenceItem,
    FactRequirement,
    FigurantCandidate,
    Finding,
    InvestigationIntent,
    InvestigationQuery,
    InvestigationResult,
    Limits,
    PersonSubject,
    QueryKind,
    RetrieverCapabilities,
    ScenarioName,
    Status,
    Subject,
)
from investigation.runs import InMemoryRunStore
from investigation.scenarios.base import PlannedQuery, ScenarioView
from investigation.scenarios.registry import ScenarioRegistry
from investigation.service import (
    NOT_STORED,
    InvestigationService,
    RunBusyError,
    RunStoreError,
    UnknownSourceError,
)
from investigation.services.model import ModelReply

KEY = "иван петрович сидоров"
QUESTION = "Что известно про Ивана Петровича Сидорова?"
DOSSIER = frozenset({"role", "charges", "politics", "rf_list", "latest_news"})
TEXT = frozenset({"role", "charges", "latest_news", "corroboration"})
Answers = Callable[[InvestigationQuery], list[EvidenceItem]]


class ProcessDied(BaseException):
    """What a killed process looks like to the code that was running: nothing catches it."""


class Fake:
    def __init__(
        self,
        name: str,
        answers: Answers,
        *,
        kinds: Sequence[QueryKind],
        facts: frozenset[str],
        network: bool = False,
    ) -> None:
        self.name = name
        self.capabilities = RetrieverCapabilities(
            source="telegram" if network else "local",
            local=not network,
            network=network,
            kinds=frozenset(kinds),
            facts=facts,
        )
        self.answers = answers
        self.calls: list[InvestigationQuery] = []

    async def search(self, query: InvestigationQuery) -> list[EvidenceItem]:
        self.calls.append(query)
        return self.answers(query)


def fact(name: str, value: str) -> EvidenceItem:
    return EvidenceItem(
        source_type="entity_group",
        source_id="entity_groups",
        entity_key=KEY,
        text=f"{name}: {value}",
        fact=name,
        value=value,
    )


# Everything but the charges: the follow-up will look for them in the texts.
KNOWN = [
    EvidenceItem(
        source_type="entity_group",
        source_id="entity_groups",
        entity_key=KEY,
        text="Иван Петрович Сидоров: 3",
        fact="identity",
        value="Иван Петрович Сидоров",
    ),
    fact("role", "figurant"),
    fact("politics", "political"),
    fact("rf_list", "full"),
    fact("latest_news", "sentence"),
]


def people(answers: Answers | None = None) -> Fake:
    def default(query: InvestigationQuery) -> list[EvidenceItem]:
        return list(KNOWN) if query.kind == "person" else []

    return Fake(
        "entity_groups", answers or default, kinds=("person", "publication_terms"), facts=DOSSIER
    )


def channel(name: str, answers: Answers | None = None) -> Fake:
    return Fake(name, answers or (lambda query: []), kinds=("keywords",), facts=TEXT, network=True)


def stored_run(*retrievers: Fake, limits: Limits | None = None) -> tuple[InMemoryRunStore, str]:
    """A run whose process died in its follow-up round: planned, one round finished."""
    store = InMemoryRunStore()
    dying = people(lambda query: list(KNOWN) if query.kind == "person" else _die())
    service = InvestigationService(retrievers=[dying, *retrievers], runs=store, limits=limits)
    with pytest.raises(ProcessDied):
        asyncio.run(service.investigate(QUESTION))
    (run_id,) = store._runs
    return store, run_id


def _die() -> list[EvidenceItem]:
    raise ProcessDied


# -- the limits and the flags of now -------------------------------------------------------------


class CountingModel:
    def __init__(self) -> None:
        self.asked = 0

    def ask(self, **_: object) -> ModelReply:
        self.asked += 1
        return ModelReply(text='{"sentences": []}')


def test_a_resumed_run_is_held_to_the_limits_of_now_not_to_the_ones_it_was_stored_with() -> None:
    """Stored when a model and forty searches were allowed; resumed by a service that
    allows no model and one search. The stored limits must not bring the old ones back."""
    store, run_id = stored_run(limits=Limits(max_llm_calls=2, max_searches=40))
    model = CountingModel()
    outside = [channel(f"telegram:tg-{number}") for number in range(4)]
    strict = InvestigationService(
        retrievers=[people(), *outside],
        runs=store,
        model=model,
        limits=Limits(max_llm_calls=0, max_searches=2),
    )

    result = asyncio.run(strict.resume(run_id))

    assert model.asked == 0
    # One search was made before the stop; one more is all the ceiling of now allows.
    assert result.usage.search_calls <= 2
    assert sum(len(fake.calls) for fake in outside) <= 1


def test_a_resumed_run_can_be_kept_local_and_its_sources_only_narrowed() -> None:
    store, run_id = stored_run()
    outside = channel("telegram:tg-chan")
    service = InvestigationService(retrievers=[people(), outside], runs=store)

    result = asyncio.run(service.resume(run_id, local_only=True))

    assert outside.calls == []
    assert result.usage.network_searches == 0


def test_a_source_given_to_a_resume_is_checked_and_kept_to() -> None:
    store, run_id = stored_run()
    first, second = channel("telegram:tg-first"), channel("telegram:tg-second")
    service = InvestigationService(retrievers=[people(), first, second], runs=store)

    with pytest.raises(UnknownSourceError):
        asyncio.run(service.resume(run_id, sources=["not-registered"]))
    # Refused before anything was done: the run is still there to resume.
    assert store.load(run_id) is not None and store.load(run_id).status == "running"  # type: ignore[union-attr]

    asyncio.run(service.resume(run_id, sources=["local", "telegram:tg-first"]))

    assert first.calls and second.calls == []


def test_sources_outside_the_run_s_own_are_refused_not_widened() -> None:
    store = InMemoryRunStore()
    dying = people(lambda query: list(KNOWN) if query.kind == "person" else _die())
    outside = channel("telegram:tg-chan")
    service = InvestigationService(retrievers=[dying, outside], runs=store)
    with pytest.raises(ProcessDied):
        asyncio.run(service.investigate(QUESTION, sources=["local"]))
    (run_id,) = store._runs

    with pytest.raises(UnknownSourceError, match="outside the run's own"):
        asyncio.run(
            InvestigationService(retrievers=[people(), outside], runs=store).resume(
                run_id, sources=["telegram"]
            )
        )
    assert outside.calls == []


# -- one process a run -----------------------------------------------------------------------------


def test_a_run_another_process_works_on_cannot_be_resumed() -> None:
    store, run_id = stored_run()
    service = InvestigationService(retrievers=[people()], runs=store)
    # Another process holds the run.
    assert store.claim(run_id) is True

    with pytest.raises(RunBusyError):
        asyncio.run(service.resume(run_id))

    store.release(run_id)
    result = asyncio.run(service.resume(run_id))
    assert result.status == "partial"
    # The run is let go when it ends: its result is read, and it can be asked for again.
    assert store.claim(run_id) is True


def test_two_resumes_at_once_make_each_search_once() -> None:
    store, run_id = stored_run()
    started = threading.Event()

    def slow(query: InvestigationQuery) -> list[EvidenceItem]:
        if query.kind == "person":
            return list(KNOWN)
        started.set()
        time.sleep(0.2)
        return []

    shared = people(slow)

    async def both() -> list[object]:
        service = InvestigationService(retrievers=[shared], runs=store)
        return list(
            await asyncio.gather(
                service.resume(run_id), service.resume(run_id), return_exceptions=True
            )
        )

    first, second = asyncio.run(both())

    outcomes = sorted(type(outcome).__name__ for outcome in (first, second))
    assert outcomes == ["InvestigationResult", "RunBusyError"]
    asked = [repr(query) for query in shared.calls]
    assert len(asked) == len(set(asked))


def test_a_late_write_cannot_replace_the_result_of_a_run_that_ended() -> None:
    store = InMemoryRunStore()
    service = InvestigationService(retrievers=[people(lambda query: list(KNOWN))], runs=store)
    result = asyncio.run(service.investigate(QUESTION))
    assert result.run_id is not None
    ended = store.load(result.run_id)
    assert ended is not None
    kept = (ended.status, ended.result, ended.progress)

    # What a branch of a stopped graph may still write.
    store.save_progress(result.run_id, scenario="x", round_=9, usage={}, progress={"done": ["x"]})
    store.update_progress(result.run_id, lambda progress: {"done": ["y"]})
    store.finish(result.run_id, status="failed", scenario=None, usage={}, result={})

    after = store.load(result.run_id)
    assert after is not None and (after.status, after.result, after.progress) == kept


# -- a stop in the middle of a round ---------------------------------------------------------------


def test_a_run_stopped_right_after_its_plan_makes_the_planned_searches_when_resumed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The plan was saved and the process died before a search ran. Resumed from the
    plan with nothing found, the run must make the plan's searches — not report that the
    person is unknown."""
    store = InMemoryRunStore()
    searched = people()
    planned = nodes.plan

    async def plan_then_die(*args: object) -> dict[str, object]:
        await planned(*args)  # type: ignore[arg-type]
        raise ProcessDied

    # The graph takes its nodes when it is built.
    monkeypatch.setattr(nodes, "plan", plan_then_die)
    dying = InvestigationService(retrievers=[searched], runs=store)
    monkeypatch.setattr(nodes, "plan", planned)
    with pytest.raises(ProcessDied):
        asyncio.run(dying.investigate(QUESTION))
    (run_id,) = store._runs
    assert searched.calls == []

    result = asyncio.run(InvestigationService(retrievers=[searched], runs=store).resume(run_id))

    assert next(query.kind for query in searched.calls) == "person"
    assert result.subject is not None and result.subject.entity_key == KEY
    assert {finding.fact for finding in result.findings} >= {"role", "politics"}


def test_a_search_that_ended_before_the_stop_is_kept_and_not_made_again() -> None:
    """Two searches of one round: the first ends, the process dies in the second. The
    first one's answer was written as it ended."""
    store = InMemoryRunStore()
    first_done = asyncio.Event()

    def first(query: InvestigationQuery) -> list[EvidenceItem]:
        first_done.set()
        return list(KNOWN) if query.kind == "person" else []

    class Second(Fake):
        async def search(self, query: InvestigationQuery) -> list[EvidenceItem]:
            self.calls.append(query)
            await first_done.wait()
            # Let the first search's record be written, as a real stop would find it.
            for _ in range(20):
                await asyncio.sleep(0.01)
            raise ProcessDied

    quick = people(first)
    dying = Second(
        "stored_publications", lambda query: [], kinds=("person",), facts=frozenset({"identity"})
    )
    with pytest.raises(ProcessDied):
        asyncio.run(
            InvestigationService(retrievers=[quick, dying], runs=store).investigate(QUESTION)
        )
    (run_id,) = store._runs
    progress = store.load(run_id).progress  # type: ignore[union-attr]
    assert progress is not None
    assert len(progress["done"]) == 1 and len(progress["found"]) == len(KNOWN)
    assert progress["usage"]["search_calls"] == 1

    quick.calls.clear()
    other = Fake(
        "stored_publications", lambda query: [], kinds=("person",), facts=frozenset({"identity"})
    )
    result = asyncio.run(InvestigationService(retrievers=[quick, other], runs=store).resume(run_id))

    # The search that had ended is not made again; the one that had not is.
    assert [query.kind for query in quick.calls if query.kind == "person"] == []
    assert [query.kind for query in other.calls] == ["person"]
    assert result.subject is not None and result.subject.entity_key == KEY
    # Counted once: one search before the stop, the rest after.
    assert result.usage.search_calls == 1 + len(other.calls) + len(quick.calls)


# -- the optional summary and the deadline ---------------------------------------------------------


class SlowModel:
    def __init__(self, seconds: float) -> None:
        self._seconds = seconds
        self.asked = 0

    def ask(self, **_: object) -> ModelReply:
        self.asked += 1
        time.sleep(self._seconds)
        return ModelReply(text='{"sentences": [{"text": "x", "evidence": ["E1"]}]}')


def _dossier(model: object | None, *, timeout: int) -> InvestigationResult:
    whole = [*KNOWN, fact("charges", "280")]
    service = InvestigationService(
        retrievers=[people(lambda query: list(whole))],
        model=model,  # type: ignore[arg-type]
        limits=Limits(timeout_seconds=timeout, max_llm_calls=1),
    )
    return asyncio.run(service.investigate(QUESTION, store=False))


def test_a_summary_that_outlives_the_deadline_costs_the_summary_not_the_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same evidence with a model that does not answer in time and without a model:
    the status and the findings are the same. Before, the whole run ended `failed` with
    nothing, ten seconds after its deadline."""
    monkeypatch.setattr(nodes, "_may_ask", lambda state, tools: state.get("evidence") is not None)
    without = _dossier(None, timeout=1)
    model = SlowModel(3)

    with_slow = _dossier(model, timeout=1)

    assert model.asked == 1
    # The result was ready at the deadline, not when the model let go.
    assert with_slow.usage.elapsed_ms < 2_500
    assert (with_slow.status, with_slow.findings) == (without.status, without.findings)
    assert with_slow.status == "completed" and with_slow.model_summary is None
    assert nodes.SUMMARY_OUT_OF_TIME in with_slow.warnings


def test_a_summary_is_not_begun_after_the_deadline() -> None:
    """The person's facts came at once; a channel kept the round waiting to the deadline.
    The report then has evidence, a model, calls left — and no time."""

    class Stuck(Fake):
        async def search(self, query: InvestigationQuery) -> list[EvidenceItem]:
            self.calls.append(query)
            await asyncio.sleep(30)
            return []

    model = CountingModel()
    whole = [*KNOWN, fact("charges", "280")]
    stuck = Stuck(
        "telegram:tg-stuck", lambda query: [], kinds=("keywords",), facts=TEXT, network=True
    )
    service = InvestigationService(
        retrievers=[people(lambda query: list(whole)), stuck],
        model=model,
        limits=Limits(timeout_seconds=1, search_timeout_seconds=20, max_llm_calls=5),
    )

    result = asyncio.run(service.investigate(QUESTION, store=False))

    assert stuck.calls and result.evidence
    assert nodes.SUMMARY_OUT_OF_TIME in result.warnings
    # The intake asked once; the report did not ask at all.
    assert model.asked == 1 and result.model_summary is None
    # And no call is counted for a summary nobody asked for.
    assert result.usage.llm_calls == 1
    assert result.status != "failed"


# -- a local search that cannot be stopped ---------------------------------------------------------


def test_a_local_search_past_its_time_keeps_its_slot_until_its_work_ends() -> None:
    """A local retriever works in a thread. Its wait ends at the timeout, its work does
    not; were the slot freed at once, the next search would run beside it and the bound
    on parallel searches would hold on paper only."""
    working = 0
    most = 0
    lock = threading.Lock()

    def blocking(seconds: float) -> None:
        nonlocal working, most
        with lock:
            working += 1
            most = max(most, working)
        time.sleep(seconds)
        with lock:
            working -= 1

    class Local(Fake):
        async def search(self, query: InvestigationQuery) -> list[EvidenceItem]:
            self.calls.append(query)
            await asyncio.to_thread(blocking, 1.6 if self.name == "entity_groups" else 0.1)
            return []

    slow = Local("entity_groups", lambda query: [], kinds=("person",), facts=DOSSIER)
    quick = Local(
        "stored_publications", lambda query: [], kinds=("person",), facts=frozenset({"identity"})
    )
    service = InvestigationService(
        retrievers=[slow, quick],
        limits=Limits(max_parallel_searches=1, search_timeout_seconds=1, timeout_seconds=10),
    )

    result = asyncio.run(service.investigate(QUESTION, store=False))

    assert slow.calls and quick.calls
    assert result.usage.failed_searches == 1
    assert most == 1


# -- the second review: what a resumed run is held to, again ---------------------------------------


def _stopped_after_its_plan(
    monkeypatch: pytest.MonkeyPatch, *retrievers: Fake
) -> tuple[InMemoryRunStore, str]:
    """A run whose process died once its plan was saved, before any search."""
    store = InMemoryRunStore()
    planned = nodes.plan

    async def plan_then_die(*args: object) -> dict[str, object]:
        await planned(*args)  # type: ignore[arg-type]
        raise ProcessDied

    monkeypatch.setattr(nodes, "plan", plan_then_die)
    dying = InvestigationService(retrievers=list(retrievers), runs=store)
    monkeypatch.setattr(nodes, "plan", planned)
    with pytest.raises(ProcessDied):
        asyncio.run(dying.investigate(QUESTION))
    (run_id,) = store._runs
    return store, run_id


def test_the_searches_a_resumed_run_still_owes_are_within_the_ceiling_of_now(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A plan of several searches, none made; resumed with a ceiling of one. The plan's
    searches are not a debt to be paid whatever the ceiling."""
    retrievers = [people(), *(channel(f"telegram:tg-{number}") for number in range(4))]
    store, run_id = _stopped_after_its_plan(monkeypatch, *retrievers)
    owed = store.load(run_id).progress["tasks"]  # type: ignore[index,union-attr]
    assert len(owed) >= 4

    result = asyncio.run(
        InvestigationService(retrievers=retrievers, runs=store).resume(run_id, max_searches=1)
    )

    assert sum(len(fake.calls) for fake in retrievers) == 1
    assert result.usage.search_calls == 1
    assert any("поисков прерванного раунда не выполнено" in warning for warning in result.warnings)


def test_a_budget_already_spent_leaves_a_resumed_run_nothing_to_search() -> None:
    store, run_id = stored_run()
    spent = store.load(run_id).progress["usage"]["search_calls"]  # type: ignore[index,union-attr]
    again = people()
    outside = channel("telegram:tg-chan")

    result = asyncio.run(
        InvestigationService(
            retrievers=[again, outside], runs=store, limits=Limits(max_searches=spent)
        ).resume(run_id)
    )

    assert again.calls == [] and outside.calls == []
    assert result.usage.search_calls == spent


def test_what_a_resume_narrowed_holds_for_the_next_resume_too() -> None:
    """Resumed local-only, stopped again before its round ended, resumed with no flag:
    the run must not come back with the network it had at first."""
    store, run_id = stored_run()
    outside = channel("telegram:tg-chan")
    dying = people(lambda query: list(KNOWN) if query.kind == "person" else _die())

    with pytest.raises(ProcessDied):
        asyncio.run(
            InvestigationService(
                retrievers=[dying, outside], runs=store, limits=Limits(max_followups=1)
            ).resume(run_id, local_only=True, max_searches=7)
        )
    kept = store.load(run_id).progress  # type: ignore[union-attr]
    assert kept is not None and kept["local_only"] is True
    assert kept["limits"]["max_searches"] == 7 and kept["limits"]["max_followups"] == 1

    result = asyncio.run(
        InvestigationService(retrievers=[people(), outside], runs=store).resume(run_id)
    )

    assert outside.calls == []
    assert result.usage.network_searches == 0


def test_a_cancelled_run_is_not_let_go_while_its_local_search_still_works() -> None:
    """Cancelling the run cancels the wait for a local search, not the thread that makes
    it. Let go at once, the run would be resumed by another process while this one still
    searched for it: two at work on one run."""
    store = InMemoryRunStore()
    started, let_go = threading.Event(), threading.Event()
    worked: list[str] = []

    def working() -> None:
        started.set()
        let_go.wait(10)
        worked.append("ended")

    class Local(Fake):
        async def search(self, query: InvestigationQuery) -> list[EvidenceItem]:
            self.calls.append(query)
            await asyncio.to_thread(working)
            return []

    local = Local("entity_groups", lambda query: [], kinds=("person",), facts=DOSSIER)
    other = people()

    async def scenario() -> None:
        first = asyncio.ensure_future(
            InvestigationService(retrievers=[local], runs=store).investigate(QUESTION)
        )
        assert await asyncio.to_thread(started.wait, 5)
        first.cancel()
        await asyncio.sleep(0.1)
        (run_id,) = store._runs

        # The worker is alive: the run is still this process's.
        with pytest.raises(RunBusyError):
            await InvestigationService(retrievers=[other], runs=store).resume(run_id)
        assert other.calls == [] and worked == []

        let_go.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert worked == ["ended"]
        # Now it can be resumed, and the search that never answered is made.
        result = await InvestigationService(retrievers=[other], runs=store).resume(run_id)
        assert next(query.kind for query in other.calls) == "person"
        assert result.run_id == run_id

    asyncio.run(scenario())


class Rounds:
    """A scenario that asks other words in every round: «Эхо», «Эхо 1», «Эхо 2»."""

    name: ScenarioName = "person_dossier"
    facts: ClassVar[dict[str, FactRequirement]] = {
        "motto": FactRequirement(key="motto", description="девиз")
    }

    def intent_by_rules(self, question: str) -> InvestigationIntent | None:
        return InvestigationIntent(scenario="person_dossier", subject_name="Эхо")

    def subject_of(self, intent: InvestigationIntent) -> PersonSubject:
        return PersonSubject(name="Эхо")

    def subject_name(self, intent: InvestigationIntent) -> str:
        return "Эхо"

    def initial_queries(self, intent: InvestigationIntent, subject: object) -> list[PlannedQuery]:
        return [
            PlannedQuery(query=InvestigationQuery(kind="keywords", name="Эхо"), purpose="motto")
        ]

    def settle(self, view: ScenarioView) -> Subject:
        return Subject(name="Эхо")

    def requirements(self, view: ScenarioView) -> list[FactRequirement]:
        return list(self.facts.values())

    def assess(self, view: ScenarioView) -> CoverageAssessment:
        words = InvestigationQuery(kind="keywords", name=f"Эхо {view.rounds + 1}", fact="motto")
        return CoverageAssessment(enough=False, missing=["девиз"], follow_up_queries=[words])

    def findings(self, view: ScenarioView) -> list[Finding]:
        return []

    def candidates(self, view: ScenarioView) -> list[FigurantCandidate]:
        return []

    def status(self, view: ScenarioView, assessment: CoverageAssessment) -> Status:
        return "partial"


def test_a_run_stopped_after_a_follow_up_s_searches_goes_on_with_the_next_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first follow-up made its searches and the process died before their coverage.
    Resumed a round behind, the run would plan the same searches, find them made and end:
    the second round's other words would never be asked."""
    store = InMemoryRunStore()
    lexical = Fake(
        "publications_fts", lambda query: [], kinds=("keywords",), facts=frozenset({"motto"})
    )
    merged = nodes.merge_evidence
    merges = 0

    async def merge_then_die(*args: Any) -> dict[str, Any]:
        nonlocal merges
        merges += 1
        if merges == 2:
            raise ProcessDied
        return await merged(*args)

    def service() -> InvestigationService:
        return InvestigationService(
            retrievers=[lexical],
            scenarios=ScenarioRegistry([Rounds()]),
            runs=store,
            limits=Limits(max_followups=2),
        )

    monkeypatch.setattr(nodes, "merge_evidence", merge_then_die)
    dying = service()
    monkeypatch.setattr(nodes, "merge_evidence", merged)
    with pytest.raises(ProcessDied):
        asyncio.run(dying.investigate("эхо?"))
    assert [query.name for query in lexical.calls] == ["Эхо", "Эхо 1"]
    (run_id,) = store._runs
    assert store.load(run_id).progress["usage"]["followup_rounds"] == 1  # type: ignore[index,union-attr]
    lexical.calls.clear()

    result = asyncio.run(service().resume(run_id))

    # The first round is not asked again; the second is.
    assert [query.name for query in lexical.calls] == ["Эхо 2"]
    assert result.usage.followup_rounds == 2 and result.usage.search_calls == 3


def test_a_store_that_cannot_create_the_record_does_not_put_the_question_in_a_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A database error carries its statement's parameters. Raised out of the service,
    it would be logged whole by whoever catches it — with the question in it."""
    marker = "Засекреченного Маркера"

    class Refusing(InMemoryRunStore):
        def create(self, run_id: str, *, question: str, request: dict[str, Any]) -> None:
            raise RuntimeError(f"INSERT INTO investigation_runs … parameters: {question!r}")

    service = InvestigationService(retrievers=[people(lambda query: list(KNOWN))], runs=Refusing())

    with caplog.at_level(logging.DEBUG):
        result = asyncio.run(service.investigate(f"Что известно про {marker}?"))

    logged = "\n".join(
        f"{record.getMessage()} {record.exc_text or ''} {record.exc_info or ''}"
        for record in caplog.records
    )
    assert marker not in logged and "Засекреченного" not in logged
    assert "method=create error=RuntimeError" in logged
    # The run was made, unstored, and says so.
    assert result.status != "failed" and result.run_id is None
    assert NOT_STORED in result.warnings


# -- the third review ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stopped_in_follow_up", "ceiling_now", "asked_before"),
    [
        # The first follow-up was stored and nothing of it searched; no follow-up now.
        (1, 0, ["Эхо"]),
        # The second one was stored; one follow-up is all that is allowed now.
        (2, 1, ["Эхо", "Эхо 1"]),
    ],
)
def test_a_stored_follow_up_is_not_made_under_a_ceiling_that_no_longer_allows_it(
    monkeypatch: pytest.MonkeyPatch,
    stopped_in_follow_up: int,
    ceiling_now: int,
    asked_before: list[str],
) -> None:
    """A round was stored as it started and the process died before its searches. The
    round was allowed then; resumed under a lower ceiling it is not, and its searches —
    of a channel as much as of the database — must not be made as a debt."""
    store = InMemoryRunStore()
    lexical = Fake(
        "publications_fts", lambda query: [], kinds=("keywords",), facts=frozenset({"motto"})
    )
    started = nodes.follow_up
    rounds = 0

    async def start_then_die(*args: Any) -> dict[str, Any]:
        nonlocal rounds
        update = await started(*args)
        rounds += 1
        if rounds == stopped_in_follow_up:
            raise ProcessDied
        return update

    def service(limits: Limits) -> InvestigationService:
        return InvestigationService(
            retrievers=[lexical],
            scenarios=ScenarioRegistry([Rounds()]),
            runs=store,
            limits=limits,
        )

    monkeypatch.setattr(nodes, "follow_up", start_then_die)
    dying = service(Limits(max_followups=2))
    monkeypatch.setattr(nodes, "follow_up", started)
    with pytest.raises(ProcessDied):
        asyncio.run(dying.investigate("эхо?"))
    assert [query.name for query in lexical.calls] == asked_before
    (run_id,) = store._runs
    stored = store.load(run_id).progress  # type: ignore[union-attr]
    assert stored is not None and stored["usage"]["followup_rounds"] == stopped_in_follow_up
    assert [task["round"] for task in stored["tasks"]] == [stopped_in_follow_up]
    lexical.calls.clear()

    result = asyncio.run(service(Limits(max_followups=2)).resume(run_id, max_followups=ceiling_now))

    assert lexical.calls == []
    # What was spent stays spent.
    assert result.usage.search_calls == len(asked_before)
    assert any("лимит дополнительных раундов" in warning for warning in result.warnings)


class FailingOnce(InMemoryRunStore):
    """A store that refuses one change of the progress — the next one — when told to."""

    def __init__(self) -> None:
        super().__init__()
        self.refuse_next = False

    def update_progress(self, run_id: str, change: Any) -> None:
        if self.refuse_next:
            self.refuse_next = False
            raise RuntimeError("UPDATE investigation_runs … parameters: Ивана Петровича Сидорова")
        super().update_progress(run_id, change)


def test_a_narrowing_the_store_did_not_take_stops_the_resume_before_any_search(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The run was to be kept local from now on, and the store refused the record. Going
    on would search under limits the store does not hold: stopped after that, the next
    resume would have the network back."""
    store = FailingOnce()
    dying = people(lambda query: list(KNOWN) if query.kind == "person" else _die())
    outside = channel("telegram:tg-chan")
    with pytest.raises(ProcessDied):
        asyncio.run(
            InvestigationService(retrievers=[dying, outside], runs=store).investigate(QUESTION)
        )
    (run_id,) = store._runs
    again, outside_again = people(), channel("telegram:tg-chan")
    service = InvestigationService(retrievers=[again, outside_again], runs=store)

    store.refuse_next = True
    with caplog.at_level(logging.DEBUG), pytest.raises(RunStoreError) as refused:
        asyncio.run(service.resume(run_id, local_only=True))

    assert again.calls == [] and outside_again.calls == []
    # The error names the run and nothing of it; the store's own text is in no log.
    assert str(refused.value) == run_id and refused.value.__cause__ is None
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "Сидорова" not in logged and "error=RuntimeError" in logged
    # Nothing was changed and the run was let go: it can be resumed when the store works.
    kept = store.load(run_id)
    assert kept is not None and kept.status == "running" and kept.progress["local_only"] is False  # type: ignore[index]
    before = kept.progress["usage"]["network_searches"]  # type: ignore[index]
    result = asyncio.run(service.resume(run_id, local_only=True))
    # Local from here on: no channel is asked beyond what the run had asked at first.
    assert outside_again.calls == [] and result.usage.network_searches == before


class SlowClaim(InMemoryRunStore):
    """A store whose claim waits at a gate, as one that waits for the database does."""

    def __init__(self) -> None:
        super().__init__()
        self.entered, self.gate = threading.Event(), threading.Event()
        self.gate.set()

    def claim(self, run_id: str) -> bool:
        self.entered.set()
        self.gate.wait(10)
        return super().claim(run_id)


def test_a_run_cancelled_while_it_was_being_claimed_is_not_left_claimed() -> None:
    """The cancel stops the wait for the claim, not the thread that takes it: the thread
    goes on and takes the run. With nobody to let it go, every later resume would be
    told the run is busy — by a process that does not work on it."""
    store = SlowClaim()
    dying = people(lambda query: list(KNOWN) if query.kind == "person" else _die())
    with pytest.raises(ProcessDied):
        asyncio.run(InvestigationService(retrievers=[dying], runs=store).investigate(QUESTION))
    (run_id,) = store._runs
    searched = people()
    service = InvestigationService(retrievers=[searched], runs=store)

    async def scenario() -> None:
        store.entered.clear()
        store.gate.clear()
        resuming = asyncio.ensure_future(service.resume(run_id))
        assert await asyncio.to_thread(store.entered.wait, 5)
        resuming.cancel()
        await asyncio.sleep(0.05)
        # The claim is still being taken; the cancel waits for it.
        assert not resuming.done()
        store.gate.set()
        with pytest.raises(asyncio.CancelledError):
            await resuming
        assert searched.calls == []

    asyncio.run(scenario())

    # Taken by the thread, and given back: the run is nobody's.
    assert store.claim(run_id) is True
    store.release(run_id)
    result = asyncio.run(service.resume(run_id))
    assert result.run_id == run_id and result.status == "partial"
