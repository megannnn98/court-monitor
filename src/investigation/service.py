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

from investigation import telemetry
from investigation.graph import SLOTS, build_graph
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
# Beyond the run's own deadline, the hard stop of the whole graph: the nodes end their
# work at the deadline, this only catches a node that would not.
_GRACE_SECONDS = 10


class RunNotFoundError(LookupError):
    pass


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
        telemetry.emit("started", run_id=run_id, local_only=local_only, sources=len(sources))
        return await self._run(initial, question=question)

    async def resume(self, run_id: str) -> InvestigationResult:
        """A stored run carried on from its last progress; a finished one as it ended.
        The searches its progress holds are not made again."""
        stored = self.get(run_id)
        if stored is not None and stored.status in FINISHED and stored.result is not None:
            return InvestigationResult.model_validate(stored.result)
        assert self._runs is not None
        run = self._runs.load(run_id)
        if run is None or run.progress is None:
            raise RunNotFoundError(run_id)
        state = restore(run.progress)
        limits = state.get("limits") or self._limits
        now = time.time()
        state.update(
            {
                "run_id": run_id,
                "resumed": True,
                "started_at": now,
                "deadline": now + limits.timeout_seconds,
            }
        )
        if state.get("intent") is None or state.get("subject_ref") is None:
            # Stopped before it was planned: nothing found yet, start it again.
            state.pop("resumed")
        telemetry.emit("started", run_id=run_id, resumed=True)
        return await self._run(state, question=state.get("question", run.question))

    def get(self, run_id: str) -> Any:
        """The stored run, or None; `RunNotFoundError` when nothing stores runs."""
        if self._runs is None:
            raise RunNotFoundError(run_id)
        return self._runs.load(run_id)

    async def _run(self, initial: InvestigationState, *, question: str) -> InvestigationResult:
        limits = initial.get("limits") or self._limits
        run_id = initial.get("run_id")
        started = time.perf_counter()
        config: RunnableConfig = {
            "recursion_limit": _BASE_STEPS + _STEPS_PER_ROUND * limits.max_followups,
            "configurable": {SLOTS: asyncio.Semaphore(limits.max_parallel_searches)},
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

    def resume_sync(self, run_id: str) -> InvestigationResult:
        return asyncio.run(self.resume(run_id))


def restore(progress: dict[str, Any]) -> InvestigationState:
    """A stored progress as a state again: each key back to its model."""
    hints = get_type_hints(InvestigationState)
    state: dict[str, Any] = {}
    for key in RESUMABLE:
        if key in progress:
            state[key] = TypeAdapter(hints[key]).validate_python(progress[key])
    return state  # type: ignore[return-value]


__all__ = ["InvestigationService", "RunNotFoundError", "UnknownSourceError", "dump_state"]
