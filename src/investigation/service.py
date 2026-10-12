"""The one door to an investigation: a question in, a typed result out.

The service owns the graph, its limits and the optional store of runs. Whatever goes
wrong inside is a result with status `failed`, never an exception and never an empty
success. A request may lower the configured limits for its run, never raise them, and
names only sources the service holds.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from typing import Any, get_type_hints

from langchain_core.runnables import RunnableConfig
from pydantic import TypeAdapter

from investigation import intent as intent_rules
from investigation import planner, telemetry
from investigation.graph import SLOTS, UNFINISHED, build_graph
from investigation.models import InvestigationResult, Limits, ScenarioName, Usage
from investigation.nodes import RESUMABLE
from investigation.runs import FINISHED, RunStore, new_run_id
from investigation.scenarios.registry import ScenarioRegistry
from investigation.services.model import InvestigationModel
from investigation.services.retrieval import (
    InvestigationRetriever,
    RetrieverRegistry,
    UnknownSourceError,
)
from investigation.state import InvestigationState, dump_state

# Each round is a handful of supersteps; this is far above any run the limits allow
# and still stops a graph that would not end.
_STEPS_PER_ROUND = 6
_BASE_STEPS = 12
NOT_STORED = "запуск не сохранён: хранилище не приняло запись, результат есть только в этом ответе"
# What a resumed run is held to, stored again when it is narrowed.
_NARROWED = ("limits", "local_only", "sources", "intent", "tasks", "warnings")
# Beyond the run's own deadline, the hard stop of the whole graph: the nodes end their
# work at the deadline, this only catches a node that would not.
_GRACE_SECONDS = 10


class RunNotFoundError(LookupError):
    pass


class RunStoreError(RuntimeError):
    """The store did not take what a run must have stored before it goes on. Carries
    the run id only: the store's own error holds the run's data."""


class RunBusyError(RuntimeError):
    """Another process works on this run now: two would make the same searches twice
    and write over each other."""


class InvestigationService:
    def __init__(
        self,
        *,
        retrievers: Sequence[InvestigationRetriever],
        model: InvestigationModel | None = None,
        limits: Limits | None = None,
        scenarios: ScenarioRegistry | None = None,
        runs: RunStore | None = None,
    ) -> None:
        self._retrievers = RetrieverRegistry(retrievers)
        self._runs = runs
        self._graph = build_graph(self._retrievers, model=model, scenarios=scenarios, runs=runs)
        self._limits = limits or Limits()

    @property
    def limits(self) -> Limits:
        return self._limits

    @property
    def retrievers(self) -> list[InvestigationRetriever]:
        return list(self._retrievers)

    @property
    def sources(self) -> set[str]:
        return self._retrievers.sources

    def check_sources(self, sources: Sequence[str]) -> None:
        """`UnknownSourceError` for a source this service does not hold."""
        self._retrievers.check_sources(sources)

    async def investigate(
        self,
        question: str,
        *,
        max_followups: int | None = None,
        local_only: bool = False,
        sources: Sequence[str] = (),
        scenario: ScenarioName | None = None,
        max_searches: int | None = None,
        store: bool = True,
    ) -> InvestigationResult:
        """`max_followups`, `max_searches` may lower the configured ceilings for this
        run, never raise them. An unknown source is `UnknownSourceError`."""
        self.check_sources(sources)
        limits = self._limits.narrowed(max_followups=max_followups, max_searches=max_searches)
        question = question.strip()
        run_id = new_run_id() if store and self._runs is not None else None
        now = time.time()
        initial: InvestigationState = {
            "question": question,
            "run_id": run_id,
            "limits": limits,
            "local_only": local_only,
            "sources": list(sources),
            "requested_scenario": scenario,
            "started_at": now,
            "deadline": now + limits.timeout_seconds,
            "usage": Usage(),
        }
        if run_id is not None:
            assert self._runs is not None
            try:
                await asyncio.to_thread(
                    self._runs.create,
                    run_id,
                    question=question,
                    request={
                        "local_only": local_only,
                        "sources": list(sources),
                        "scenario": scenario,
                        "limits": limits.model_dump(),
                    },
                )
            except Exception as exc:  # noqa: BLE001 - a lost record costs the record, not the run
                # Only the class: a database error carries its statement's parameters,
                # the question among them, and whoever logs it would log the question.
                telemetry.logger.warning(
                    "event=investigation_store_failed method=create error=%s", type(exc).__name__
                )
                run_id = None
                initial["run_id"] = None
                initial["warnings"] = [NOT_STORED]
        telemetry.emit("started", run_id=run_id, local_only=local_only, sources=len(sources))
        await self._claim(run_id)
        unfinished: set[asyncio.Future[Any]] = set()
        try:
            return await self._run(initial, question=question, unfinished=unfinished)
        finally:
            await self._release_when_idle(run_id, unfinished)

    async def resume(
        self,
        run_id: str,
        *,
        max_followups: int | None = None,
        max_searches: int | None = None,
        local_only: bool = False,
        sources: Sequence[str] = (),
    ) -> InvestigationResult:
        """A stored run carried on from its last progress; a finished one as it ended.
        The searches its progress holds are not made again; the ones its round still
        owed are made first.

        A resumed run is held to the service's limits of now, to its own stored ones and
        to what this call asks — whichever is lower; it stays local if it was or is asked
        to be; and its sources can only be narrowed. `RunBusyError` when another process
        works on it."""
        self.check_sources(sources)
        stored = self.get(run_id)
        if stored is not None and stored.status in FINISHED and stored.result is not None:
            return InvestigationResult.model_validate(stored.result)
        assert self._runs is not None
        await self._claim(run_id)
        unfinished: set[asyncio.Future[Any]] = set()
        try:
            # Read again, now that nobody else can write it.
            run = self._runs.load(run_id)
            if run is not None and run.status in FINISHED and run.result is not None:
                return InvestigationResult.model_validate(run.result)
            if run is None or run.progress is None:
                raise RunNotFoundError(run_id)
            state = restore(run.progress)
            limits = (
                (state.get("limits") or self._limits)
                .within(self._limits)
                .narrowed(max_followups=max_followups, max_searches=max_searches)
            )
            now = time.time()
            state.update(
                {
                    "run_id": run_id,
                    "resumed": True,
                    "limits": limits,
                    "local_only": bool(state.get("local_only")) or local_only,
                    "sources": self._narrowed_sources(state.get("sources", []), sources),
                    "started_at": now,
                    "deadline": now + limits.timeout_seconds,
                }
            )
            plan = state.get("plan")
            intent = state.get("intent")
            if intent is None or state.get("subject_ref") is None or plan is None:
                # Stopped before it was planned: nothing found yet, start it again.
                state.pop("resumed")
            else:
                state["intent"] = intent_rules.with_sources(intent, state["sources"])
                # The searches of the round in progress that were never made — a run
                # stopped right after its plan or its follow-up, or between two searches.
                # A round that ended is planned on from what is missing.
                done = set(state.get("done", []))
                may_ask = {
                    retriever.name
                    for retriever in self._retrievers.allowed(
                        local_only=state["local_only"], sources=state["sources"]
                    )
                }
                left = [
                    task
                    for task in state.get("tasks", plan.tasks)
                    if task.fingerprint not in done and task.retriever in may_ask
                ]
                # Within the ceilings of now. A follow-up round stored when more rounds
                # were allowed is not made under a ceiling that no longer allows it; the
                # searches left are counted with what the run already spent.
                owed = [task for task in left if task.round <= limits.max_followups]
                spent = (state.get("usage") or Usage()).search_calls
                state["tasks"], cut = planner.cap(owed, limits.max_searches - spent)
                warnings = list(state.get("warnings", []))
                if len(left) > len(owed):
                    warnings.append(
                        f"лимит дополнительных раундов: {len(left) - len(owed)} поисков "
                        "прерванного раунда не выполнено"
                    )
                if cut:
                    warnings.append(f"лимит поисков: {cut} поисков прерванного раунда не выполнено")
                state["warnings"] = warnings
            # What the run is held to from now on is stored before anything runs: a run
            # stopped again must not come back with the wider limits it had before.
            narrowed = dump_state(
                {key: state[key] for key in _NARROWED if key in state}  # type: ignore[literal-required]
            )
            try:
                await asyncio.to_thread(
                    self._runs.update_progress, run_id, lambda progress: {**progress, **narrowed}
                )
            except Exception as exc:  # noqa: BLE001 - told by its class: it carries the progress
                # Unlike a new run, which may go on unstored, this one must not: a search
                # made now and a stop after it would leave the store with the wider
                # limits, and the next resume would have them back.
                telemetry.logger.warning(
                    "event=investigation_store_failed method=update_progress error=%s",
                    type(exc).__name__,
                )
                raise RunStoreError(run_id) from None
            telemetry.emit("started", run_id=run_id, resumed=True)
            return await self._run(
                state, question=state.get("question", run.question), unfinished=unfinished
            )
        finally:
            await self._release_when_idle(run_id, unfinished)

    def _narrowed_sources(self, kept: Sequence[str], asked: Sequence[str]) -> list[str]:
        """The sources a resumed run keeps to: its own, narrowed by what is asked now.
        Sources that leave it nothing to ask are refused, not ignored."""
        if not asked:
            return list(kept)
        if not kept:
            return list(asked)
        own = {r.name for r in self._retrievers.allowed(local_only=False, sources=kept)}
        now = {r.name for r in self._retrievers.allowed(local_only=False, sources=asked)}
        both = sorted(own & now)
        if not both:
            raise UnknownSourceError(
                f"sources {', '.join(sorted(asked))} are outside the run's own: "
                f"{', '.join(sorted(kept))}"
            )
        return both

    async def _claim(self, run_id: str | None) -> None:
        if run_id is None or self._runs is None:
            return
        taking = asyncio.ensure_future(asyncio.to_thread(self._runs.claim, run_id))
        try:
            taken = await asyncio.shield(taking)
        except asyncio.CancelledError:
            # Cancelled while the claim was being taken: its thread goes on and may well
            # take it. Left so, the run would be held by nobody who works on it. Wait for
            # the claim to end, give back what it took, and only then let the cancel go.
            def give_back(ended: asyncio.Future[bool]) -> None:
                if not ended.cancelled() and ended.exception() is None and ended.result():
                    asyncio.ensure_future(self._release(run_id))

            try:
                taken = await asyncio.shield(taking)
            except asyncio.CancelledError:
                # Cancelled again while waiting: the giving back is left to the claim's end.
                taking.add_done_callback(give_back)
                raise
            if taken:
                await self._release(run_id)
            raise
        if not taken:
            raise RunBusyError(run_id)

    async def _release_when_idle(
        self, run_id: str | None, unfinished: set[asyncio.Future[Any]]
    ) -> None:
        """Let the run go once no local search of it still works. A search the run
        stopped waiting for — at its timeout, or because the run was cancelled — works
        on in its thread; let go before it ends, the run could be resumed by another
        process while this one still searches for it."""
        working = [work for work in unfinished if not work.done()]
        if not working:
            await self._release(run_id)
            return
        ended = asyncio.gather(*working, return_exceptions=True)
        try:
            await asyncio.shield(ended)
        except asyncio.CancelledError:
            # Cancelled again while waiting: the release is left to the work's end.
            ended.add_done_callback(lambda _: asyncio.ensure_future(self._release(run_id)))
            raise
        await self._release(run_id)

    async def _release(self, run_id: str | None) -> None:
        if run_id is None or self._runs is None:
            return
        try:
            await asyncio.to_thread(self._runs.release, run_id)
        except Exception as exc:  # noqa: BLE001 - a lost release ends with the process
            telemetry.logger.warning(
                "event=investigation_store_failed method=release error=%s", type(exc).__name__
            )

    def get(self, run_id: str) -> Any:
        """The stored run, or None; `RunNotFoundError` when nothing stores runs."""
        if self._runs is None:
            raise RunNotFoundError(run_id)
        return self._runs.load(run_id)

    async def _run(
        self,
        initial: InvestigationState,
        *,
        question: str,
        unfinished: set[asyncio.Future[Any]] | None = None,
    ) -> InvestigationResult:
        limits = initial.get("limits") or self._limits
        run_id = initial.get("run_id")
        started = time.perf_counter()
        config: RunnableConfig = {
            "recursion_limit": _BASE_STEPS + _STEPS_PER_ROUND * limits.max_followups,
            "configurable": {
                SLOTS: asyncio.Semaphore(limits.max_parallel_searches),
                UNFINISHED: set() if unfinished is None else unfinished,
            },
        }
        try:
            final = await asyncio.wait_for(
                self._graph.ainvoke(initial, config),
                limits.timeout_seconds + _GRACE_SECONDS,
            )
            result = final["result"]
            if not isinstance(result, InvestigationResult):
                raise TypeError("the graph ended without a result")
        except UnknownSourceError:
            raise
        except Exception as exc:  # noqa: BLE001 - whatever fails inside is a failed result
            telemetry.emit(
                "failed",
                duration_ms=(time.perf_counter() - started) * 1000,
                run_id=run_id,
                error=type(exc).__name__,
            )
            telemetry.logger.exception("event=investigation_failed_trace run_id=%s", run_id)
            result = InvestigationResult(
                status="failed",
                question=question,
                run_id=run_id,
                warnings=[f"исследование не выполнено: {type(exc).__name__}"],
            )
            await self._finish(result, failure=type(exc).__name__)
            return result
        telemetry.emit(
            "completed",
            duration_ms=(time.perf_counter() - started) * 1000,
            run_id=run_id,
            status=result.status,
            scenario=result.scenario,
            searches=result.usage.search_calls,
            rounds=result.usage.followup_rounds,
            evidence=len(result.evidence),
        )
        await self._finish(result)
        return result

    async def _finish(self, result: InvestigationResult, *, failure: str | None = None) -> None:
        if self._runs is None or result.run_id is None:
            return
        try:
            await asyncio.to_thread(
                self._runs.finish,
                result.run_id,
                status=result.status,
                scenario=result.scenario,
                usage=result.usage.model_dump(mode="json"),
                result=result.model_dump(mode="json"),
                failure=failure,
            )
        except Exception as exc:  # noqa: BLE001 - a lost record is logged, the result stands
            telemetry.logger.warning(
                "event=investigation_store_failed method=finish error=%s", type(exc).__name__
            )

    def investigate_sync(
        self,
        question: str,
        *,
        max_followups: int | None = None,
        local_only: bool = False,
        sources: Sequence[str] = (),
        scenario: ScenarioName | None = None,
    ) -> InvestigationResult:
        return asyncio.run(
            self.investigate(
                question,
                max_followups=max_followups,
                local_only=local_only,
                sources=sources,
                scenario=scenario,
            )
        )

    def resume_sync(
        self,
        run_id: str,
        *,
        max_followups: int | None = None,
        local_only: bool = False,
        sources: Sequence[str] = (),
    ) -> InvestigationResult:
        return asyncio.run(
            self.resume(run_id, max_followups=max_followups, local_only=local_only, sources=sources)
        )


def restore(progress: dict[str, Any]) -> InvestigationState:
    """A stored progress as a state again: each key back to its model."""
    hints = get_type_hints(InvestigationState)
    state: dict[str, Any] = {}
    for key in RESUMABLE:
        if key in progress:
            state[key] = TypeAdapter(hints[key]).validate_python(progress[key])
    return state  # type: ignore[return-value]


__all__ = [
    "InvestigationService",
    "RunBusyError",
    "RunNotFoundError",
    "RunStoreError",
    "UnknownSourceError",
    "dump_state",
]
