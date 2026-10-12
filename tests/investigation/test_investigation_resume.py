"""A stored run, stopped and carried on: what it may do when it is resumed, who may work
on it, and what survives a stop in the middle of a round.

Every stop here is a process that died — an exception nothing catches — at a named
point; the store is the in-memory one, whose contract the PostgreSQL store keeps
(`test_investigation_db.py`)."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable, Sequence

import pytest

from investigation import nodes
from investigation.models import (
    EvidenceItem,
    InvestigationQuery,
    InvestigationResult,
    Limits,
    QueryKind,
    RetrieverCapabilities,
)
from investigation.runs import InMemoryRunStore
from investigation.service import InvestigationService, RunBusyError, UnknownSourceError
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
