"""The one door to an investigation: a question in, a typed result out.

The service owns the graph and its limits. Whatever goes wrong inside is a result with
status `failed`, never an exception and never an empty success.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

from investigation.graph import build_graph
from investigation.models import InvestigationResult, Limits, Usage
from investigation.services.model import InvestigationModel
from investigation.services.retrieval import InvestigationRetriever
from investigation.state import InvestigationState

logger = logging.getLogger("investigation")

# Each round is a handful of supersteps; this is far above any run the limits allow
# and still stops a graph that would not end.
_STEPS_PER_ROUND = 6
_BASE_STEPS = 12


class InvestigationService:
    def __init__(
        self,
        *,
        retrievers: Sequence[InvestigationRetriever],
        model: InvestigationModel | None = None,
        limits: Limits | None = None,
    ) -> None:
        self._graph = build_graph(retrievers, model=model)
        self._limits = limits or Limits()

    @property
    def limits(self) -> Limits:
        return self._limits

    async def investigate(
        self,
        question: str,
        *,
        max_followups: int | None = None,
        local_only: bool = False,
    ) -> InvestigationResult:
        """`max_followups` may lower the configured ceiling for this run, never raise it."""
        limits = self._limits
        if max_followups is not None:
            limits = limits.model_copy(
                update={"max_followups": max(0, min(max_followups, limits.max_followups))}
            )
        question = question.strip()
        initial: InvestigationState = {
            "question": question,
            "limits": limits,
            "local_only": local_only,
            "usage": Usage(),
        }
        try:
            final = await self._graph.ainvoke(
                initial,
                {"recursion_limit": _BASE_STEPS + _STEPS_PER_ROUND * limits.max_followups},
            )
            result = final["result"]
            if not isinstance(result, InvestigationResult):
                raise TypeError("the graph ended without a result")
            return result
        except Exception as exc:
            logger.exception("event=investigation_failed")
            return InvestigationResult(
                status="failed",
                question=question,
                warnings=[f"исследование не выполнено: {type(exc).__name__}"],
            )

    def investigate_sync(
        self,
        question: str,
        *,
        max_followups: int | None = None,
        local_only: bool = False,
    ) -> InvestigationResult:
        return asyncio.run(
            self.investigate(question, max_followups=max_followups, local_only=local_only)
        )
