"""The investigation as a LangGraph state graph: explicit nodes, one bounded loop.

    START ─┬→ intake ─┬─ nothing to look up ──────────────────────────────→ report → END
           │          └→ plan ─→ search × task ─→ merge_evidence → coverage
           │                       ↑   in parallel, bounded             │
           │                       │                     enough, no new │
           │                       └── follow_up ←── gaps ──  search,  ─┴→ report
           └→ (resumed) merge_evidence                        or limits

The loop runs at most `Limits.max_followups` times, never repeats a search, and stops
when no new search could find what is missing. Nothing here is an agent: which node
follows which is decided by the routing functions below, from the state alone; no
model and no text found chooses a node, a retriever or a source. The graph is the same
for every scenario: what a scenario looks for is the scenario's (`scenarios/`).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send

from investigation import nodes
from investigation.models import Limits, Usage
from investigation.nodes import Toolbox
from investigation.runs import RunStore
from investigation.scenarios.registry import ScenarioRegistry, default_scenarios
from investigation.services.model import InvestigationModel
from investigation.services.retrieval import InvestigationRetriever, RetrieverRegistry
from investigation.state import InvestigationState, SearchBranch

Graph = CompiledStateGraph[InvestigationState, None, InvestigationState, InvestigationState]

MERGE = "merge_evidence"
# Where a run keeps its free search slots: `config["configurable"][SLOTS]`.
SLOTS = "search_slots"


StateNode = Callable[[InvestigationState, Toolbox], Awaitable[dict[str, Any]]]


class _Step(Protocol):
    def __call__(self, state: InvestigationState) -> Awaitable[dict[str, Any]]: ...


def _node(node: StateNode, tools: Toolbox) -> _Step:
    async def run(state: InvestigationState) -> dict[str, Any]:
        return await node(state, tools)

    return run


def build_graph(
    retrievers: Sequence[InvestigationRetriever] | RetrieverRegistry,
    *,
    model: InvestigationModel | None = None,
    scenarios: ScenarioRegistry | None = None,
    runs: RunStore | None = None,
) -> Graph:
    registry = (
        retrievers if isinstance(retrievers, RetrieverRegistry) else RetrieverRegistry(retrievers)
    )
    tools = Toolbox(
        retrievers=registry,
        scenarios=scenarios or default_scenarios(),
        model=model,
        runs=runs,
    )

    def start(state: InvestigationState) -> str:
        return MERGE if state.get("resumed") else "intake"

    def after_intake(state: InvestigationState) -> str:
        return "plan" if state.get("subject_ref") is not None else "report"

    def fan_out(state: InvestigationState) -> list[Send] | str:
        """Each task of the round in its own branch: only the tasks the planner made,
        so only queries a retriever can answer, nothing searched before."""
        limits = state.get("limits") or Limits()
        intent = state.get("intent")
        sends = [
            Send(
                "search",
                SearchBranch(
                    task=task,
                    run_id=state.get("run_id"),
                    scenario=intent.scenario if intent else "",
                    deadline=state.get("deadline", float("inf")),
                    search_timeout=float(limits.search_timeout_seconds),
                ),
            )
            for task in state.get("tasks", [])
        ]
        return sends or MERGE

    def after_coverage(state: InvestigationState) -> str:
        assessment = state.get("coverage")
        limits = state.get("limits") or Limits()
        usage = state.get("usage") or Usage()
        if (
            assessment is None
            or not assessment.follow_up_tasks
            or usage.followup_rounds >= limits.max_followups
            or usage.search_calls >= limits.max_searches
            or time.time() >= state.get("deadline", float("inf"))
        ):
            return "report"
        return "follow_up"

    async def search(state: SearchBranch, config: RunnableConfig) -> dict[str, Any]:
        slots = (config.get("configurable") or {}).get(SLOTS)
        return await nodes.search(
            state, tools, slots if isinstance(slots, asyncio.Semaphore) else None
        )

    builder = StateGraph(InvestigationState)
    builder.add_node("intake", _node(nodes.intake, tools))
    builder.add_node("plan", _node(nodes.plan, tools))
    builder.add_node("search", search)
    builder.add_node(MERGE, _node(nodes.merge_evidence, tools))
    builder.add_node("coverage", _node(nodes.coverage, tools))
    builder.add_node("follow_up", _node(nodes.follow_up, tools))
    builder.add_node("report", _node(nodes.report, tools))

    builder.add_conditional_edges(START, start, ["intake", MERGE])
    builder.add_conditional_edges("intake", after_intake, ["plan", "report"])
    builder.add_conditional_edges("plan", fan_out, ["search", MERGE])
    builder.add_edge("search", MERGE)
    builder.add_edge(MERGE, "coverage")
    builder.add_conditional_edges("coverage", after_coverage, ["follow_up", "report"])
    builder.add_conditional_edges("follow_up", fan_out, ["search", MERGE])
    builder.add_edge("report", END)
    return builder.compile()
