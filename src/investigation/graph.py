"""The investigation as a LangGraph state graph: explicit nodes, one bounded loop.

    START → intake ─┬─ no name ──────────────────────────────────────────→ report → END
                    └→ plan ─→ search × (query, retriever) ─→ merge_evidence → coverage
                                  ↑        in parallel                            │
                                  │                              enough, or the   │
                                  └──── follow_up ←── gaps ──────  limit reached ─┴→ report

The loop runs at most `Limits.max_followups` times. Nothing here is an agent: which
node follows which is decided by the two routing functions below, from the state alone.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send

from investigation import nodes
from investigation.models import Limits, Usage
from investigation.nodes import Toolbox
from investigation.services.model import InvestigationModel
from investigation.services.retrieval import InvestigationRetriever, usable
from investigation.state import InvestigationState, SearchTask

Graph = CompiledStateGraph[InvestigationState, None, InvestigationState, InvestigationState]

MERGE = "merge_evidence"


StateNode = Callable[[InvestigationState, Toolbox], Awaitable[dict[str, Any]]]


class _Step(Protocol):
    def __call__(self, state: InvestigationState) -> Awaitable[dict[str, Any]]: ...


def _node(node: StateNode, tools: Toolbox) -> _Step:
    async def run(state: InvestigationState) -> dict[str, Any]:
        return await node(state, tools)

    return run


def build_graph(
    retrievers: Sequence[InvestigationRetriever], *, model: InvestigationModel | None = None
) -> Graph:
    tools = Toolbox(retrievers={retriever.name: retriever for retriever in retrievers}, model=model)

    def after_intake(state: InvestigationState) -> str:
        return "plan" if state.get("subject") is not None else "report"

    def fan_out(state: InvestigationState) -> list[Send] | str:
        """Every query of the round to every retriever allowed, each in its own branch."""
        allowed = usable(retrievers, local_only=state.get("local_only", False))
        tasks = [
            Send("search", SearchTask(query=query, retriever=retriever.name))
            for query in state.get("queries", [])
            for retriever in allowed
        ]
        return tasks or MERGE

    def after_coverage(state: InvestigationState) -> str:
        assessment = state.get("coverage")
        limits = state.get("limits") or Limits()
        rounds = (state.get("usage") or Usage()).followup_rounds
        if assessment is None or assessment.enough or rounds >= limits.max_followups:
            return "report"
        return "follow_up"

    async def search(state: SearchTask) -> dict[str, Any]:
        return await nodes.search(state, tools)

    builder = StateGraph(InvestigationState)
    builder.add_node("intake", _node(nodes.intake, tools))
    builder.add_node("plan", _node(nodes.plan, tools))
    builder.add_node("search", search)
    builder.add_node(MERGE, _node(nodes.merge_evidence, tools))
    builder.add_node("coverage", _node(nodes.coverage, tools))
    builder.add_node("follow_up", _node(nodes.follow_up, tools))
    builder.add_node("report", _node(nodes.report, tools))

    builder.add_edge(START, "intake")
    builder.add_conditional_edges("intake", after_intake, ["plan", "report"])
    builder.add_conditional_edges("plan", fan_out, ["search", MERGE])
    builder.add_edge("search", MERGE)
    builder.add_edge(MERGE, "coverage")
    builder.add_conditional_edges("coverage", after_coverage, ["follow_up", "report"])
    builder.add_conditional_edges("follow_up", fan_out, ["search", MERGE])
    builder.add_edge("report", END)
    return builder.compile()
