"""From a scenario's queries to searches: which retriever is asked what, in what order,
and what is not asked at all.

A query goes only to a retriever that can answer it (`can_answer`): its kind, and the
fact it looks for. A search already made in this run — the same retriever, the same
query — is never made again. A ceiling on searches cuts the least urgent first.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence

from investigation.models import MAX_QUERY_LENGTH, InvestigationQuery, SearchTask
from investigation.scenarios.base import PlannedQuery
from investigation.services.retrieval import InvestigationRetriever, can_answer

# A structured lookup first, ranked text next, a source outside last.
_BY_KIND = {"person": 0, "publication_terms": 0, "unnamed": 0, "publication_people": 0}
_TEXT = 1
_NETWORK = 1
# A plan holds at most this many searches before a ceiling is even applied: one query
# can never ask a hundred sources.
MAX_TASKS_PER_ROUND = 60


class InvalidPlanError(ValueError):
    """A plan that cannot be run as it is: refused, never run in part."""


def planned(queries: Sequence[InvestigationQuery]) -> list[PlannedQuery]:
    """Follow-up queries, with what each looks for and how urgent it is."""
    return [
        PlannedQuery(
            query=query,
            purpose=query.fact or "identity",
            priority=_BY_KIND.get(query.kind, _TEXT),
        )
        for query in queries
    ]


def validate(query: PlannedQuery, *, facts: Collection[str]) -> None:
    """A query a scenario or a model wrote, checked before anything runs it."""
    if len(query.query.text) > MAX_QUERY_LENGTH:
        raise InvalidPlanError(f"query longer than {MAX_QUERY_LENGTH} characters")
    if query.purpose not in ("identity", *facts):
        raise InvalidPlanError(f"unknown fact: {query.purpose}")
    if query.query.kind == "keywords" and not query.query.text:
        raise InvalidPlanError("a text search with no words")


def assign(
    queries: Sequence[PlannedQuery],
    retrievers: Sequence[InvestigationRetriever],
    *,
    round_: int,
    done: Collection[str] = (),
    facts: Collection[str] = (),
) -> list[SearchTask]:
    """Each query to each retriever able to answer it, nothing searched before, the most
    urgent first. A query no retriever can answer is simply not searched."""
    tasks: dict[str, SearchTask] = {}
    for planned_query in queries:
        validate(planned_query, facts=facts)
        for retriever in retrievers:
            if not can_answer(retriever, planned_query.query, planned_query.purpose):
                continue
            task = SearchTask(
                query=planned_query.query,
                retriever=retriever.name,
                purpose=planned_query.purpose,
                round=round_,
                priority=planned_query.priority
                + (_NETWORK if retriever.capabilities.network else 0),
            )
            if task.fingerprint in done or task.fingerprint in tasks:
                continue
            tasks[task.fingerprint] = task
    ordered = sorted(tasks.values(), key=lambda task: task.priority)
    if len(ordered) > MAX_TASKS_PER_ROUND:
        raise InvalidPlanError(f"{len(ordered)} searches in one round")
    return ordered


def cap(tasks: Sequence[SearchTask], remaining: int) -> tuple[list[SearchTask], int]:
    """The tasks a ceiling leaves, the most urgent first, and how many it cut."""
    kept = list(tasks[: max(remaining, 0)])
    return kept, len(tasks) - len(kept)
