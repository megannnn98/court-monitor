"""The production investigation graph runs on substituted dependencies alone.

`investigation.graph.build_graph` — the graph the service runs — is given a synthetic
scenario, fake retrievers, a fake model and an in-memory run store, none of which knows
Court Monitor: no entity group, no publication, no PostgreSQL, no Telegram, no
OpenRouter. Every socket is refused for the duration of each test, so any reach for a
database or the network fails it.

What it proves: the graph's chain — intake, plan, search, merge_evidence, coverage,
follow_up, report — is the same for any implementation of its ports.
"""

from __future__ import annotations

import asyncio
import json
import socket
import time
from collections.abc import Mapping
from typing import Any, ClassVar

import pytest

from investigation.graph import build_graph
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
    RetrieverCapabilities,
    ScenarioName,
    Status,
    Subject,
    SubjectRef,
    Usage,
)
from investigation.runs import InMemoryRunStore, ProgressChange
from investigation.scenarios.base import PlannedQuery, ScenarioView
from investigation.scenarios.registry import ScenarioRegistry
from investigation.services.model import ModelReply
from investigation.state import InvestigationState

QUESTION = "Что известно о субъекте Альфа?"
SUBJECT = "Альфа"
FACTS = ("identity", "status")


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    """No database, no site, no model provider: a socket is a failure."""

    def refused(*args: object, **kwargs: object) -> None:
        raise AssertionError("the investigation reached for a socket")

    monkeypatch.setattr(socket.socket, "connect", refused)
    monkeypatch.setattr(socket.socket, "connect_ex", refused)
    monkeypatch.setattr(socket, "create_connection", refused)
    monkeypatch.setattr(socket, "getaddrinfo", refused)


# -- the synthetic scenario ----------------------------------------------------------------


class SubjectScenario:
    """A scenario Court Monitor does not have: one named subject, two facts. The first
    round asks who the subject is; a missing status is asked for once more."""

    name: ScenarioName = "test_subject"
    facts: ClassVar[Mapping[str, FactRequirement]] = {
        key: FactRequirement(key=key, description=key) for key in FACTS
    }

    def intent_by_rules(self, question: str) -> InvestigationIntent | None:
        return InvestigationIntent(scenario=self.name, subject_name=SUBJECT)

    def subject_of(self, intent: InvestigationIntent) -> SubjectRef | None:
        return None if not intent.subject_name else PersonSubject(name=intent.subject_name)

    def subject_name(self, intent: InvestigationIntent) -> str:
        return intent.subject_name or ""

    def initial_queries(
        self, intent: InvestigationIntent, subject: SubjectRef
    ) -> list[PlannedQuery]:
        assert isinstance(subject, PersonSubject)
        return [
            PlannedQuery(
                query=InvestigationQuery(kind="person", name=subject.name), purpose="identity"
            )
        ]

    def settle(self, view: ScenarioView) -> Subject | None:
        return Subject(name=SUBJECT, entity_key="subject:alpha")

    def requirements(self, view: ScenarioView) -> list[FactRequirement]:
        return list(self.facts.values())

    def _known(self, view: ScenarioView) -> set[str]:
        return {item.fact for item in view.evidence if item.fact in FACTS}

    def assess(self, view: ScenarioView) -> CoverageAssessment:
        known = self._known(view)
        missing = [fact for fact in FACTS if fact not in known]
        follow_ups = [
            InvestigationQuery(kind="keywords", name=SUBJECT, terms=(fact,), fact=fact)
            for fact in missing
        ]
        return CoverageAssessment(
            enough=not follow_ups,
            known=sorted(known),
            missing=missing,
            follow_up_queries=follow_ups,
        )

    def findings(self, view: ScenarioView) -> list[Finding]:
        return [
            Finding(fact=item.fact, statement=f"{item.fact}: {item.value}", evidence=[item.ref])
            for item in view.evidence
            if item.fact in FACTS and item.value
        ]

    def candidates(self, view: ScenarioView) -> list[FigurantCandidate]:
        return []

    def status(self, view: ScenarioView, assessment: CoverageAssessment) -> Status:
        return "partial" if assessment.missing else "completed"


# -- fake retrievers, model, store ----------------------------------------------------------


def fact(name: str, value: str, *, source: str) -> EvidenceItem:
    return EvidenceItem(
        source_type="external",
        source_id=source,
        external_id=f"{name}-1",
        url=f"memory://{source}/{name}",
        entity_key="subject:alpha",
        text=f"{name}: {value}",
        fact=name,
        value=value,
    )


class Registry:
    """A retriever of an in-memory record. Answers a person query with the identity
    and a note, and a keyword query for a fact with that fact, if it holds it."""

    def __init__(
        self,
        name: str,
        record: Mapping[str, str],
        *,
        kinds: frozenset[Any] = frozenset({"person", "keywords"}),
        facts: frozenset[str] = frozenset({"status"}),
    ) -> None:
        self.name = name
        self.capabilities = RetrieverCapabilities(
            source="memory", local=True, kinds=kinds, facts=facts
        )
        self._record = record
        self.calls: list[InvestigationQuery] = []

    async def search(self, query: InvestigationQuery) -> list[EvidenceItem]:
        self.calls.append(query)
        if query.kind == "person":
            return [
                fact("identity", self._record["identity"], source=self.name),
                EvidenceItem(
                    source_type="external",
                    source_id=self.name,
                    external_id="note-1",
                    url=f"memory://{self.name}/note",
                    text=f"{SUBJECT} упоминается в заметке",
                ),
            ]
        if query.fact is not None and query.fact in self._record:
            return [fact(query.fact, self._record[query.fact], source=self.name)]
        return []


class ScriptedModel:
    """The model port answered from a script: the intake names the subject and the
    scenario, the report words one sentence citing E1."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def ask(
        self,
        *,
        system: str,
        user: str,
        schema_name: str,
        schema: dict[str, object],
        max_tokens: int,
    ) -> ModelReply:
        self.calls.append(schema_name)
        if schema_name == "investigation_intake":
            # The model is offered the registry's scenarios, not the shipped ones.
            properties = schema["properties"]
            assert isinstance(properties, dict)
            assert properties["scenario"]["enum"] == ["test_subject", None]
            body: dict[str, object] = {
                "scenario": "test_subject",
                "subject_name": SUBJECT,
                "article_id": None,
                "requested_facts": [],
            }
        else:
            body = {"sentences": [{"text": f"{SUBJECT} известен.", "evidence": ["E1"]}]}
        return ModelReply(text=json.dumps(body), input_tokens=10, output_tokens=5)


class CountingRunStore(InMemoryRunStore):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    def save_progress(self, run_id: str, **kwargs: Any) -> None:
        self.calls.append("save_progress")
        super().save_progress(run_id, **kwargs)

    def update_progress(self, run_id: str, change: ProgressChange) -> None:
        self.calls.append("update_progress")
        super().update_progress(run_id, change)


def scenarios() -> ScenarioRegistry:
    return ScenarioRegistry([SubjectScenario()])


def investigate(
    *retrievers: Registry,
    model: ScriptedModel | None = None,
    runs: CountingRunStore | None = None,
    run_id: str | None = None,
) -> InvestigationResult:
    graph = build_graph(retrievers, model=model, scenarios=scenarios(), runs=runs)
    now = time.time()
    initial: InvestigationState = {
        "question": QUESTION,
        "run_id": run_id,
        "limits": Limits(),
        "usage": Usage(),
        "started_at": now,
        "deadline": now + 60,
    }
    final = asyncio.run(graph.ainvoke(initial))
    result = final["result"]
    assert isinstance(result, InvestigationResult)
    return result


RECORD = {"identity": "Альфа, тестовый субъект", "status": "активен"}


# -- the proofs ------------------------------------------------------------------------------


def assert_the_whole_chain_ran(result: InvestigationResult, retriever: Registry) -> None:
    # report: the result is the report node's; completed, both facts found and cited.
    assert result.status == "completed"
    assert result.scenario == "test_subject"
    assert {finding.fact for finding in result.findings} == set(FACTS)
    refs = {item.ref for item in result.evidence}
    assert all(set(finding.evidence) <= refs for finding in result.findings)
    # plan → search: the first round asked who; coverage → follow_up: the second asked
    # for the missing status alone.
    assert [(query.kind, query.fact) for query in retriever.calls] == [
        ("person", None),
        ("keywords", "status"),
    ]
    assert result.usage.followup_rounds == 1
    # Every search ran once.
    assert result.usage.search_calls == len(retriever.calls) == 2
    assert len({task.fingerprint for task in result.plan.tasks}) == len(result.plan.tasks)  # type: ignore[union-attr]
    # Evidence of the fake source, merged and numbered.
    assert result.evidence and {item.source_id for item in result.evidence} == {retriever.name}
    assert [item.ref for item in result.evidence] == [
        f"E{number}" for number in range(1, len(result.evidence) + 1)
    ]


def test_the_production_graph_runs_on_fakes_without_a_model() -> None:
    record = Registry("memory_registry", RECORD)

    result = investigate(record)

    assert_the_whole_chain_ran(result, record)
    assert result.intent is not None and result.intent.read_by == "rules"
    assert result.usage.llm_calls == 0 and result.model_summary is None


def test_the_production_graph_runs_on_a_fake_model() -> None:
    record = Registry("memory_registry", RECORD)
    model = ScriptedModel()

    result = investigate(record, model=model)

    assert_the_whole_chain_ran(result, record)
    assert model.calls == ["investigation_intake", "investigation_report"]
    assert result.intent is not None and result.intent.read_by == "model"
    assert result.model_summary == f"{SUBJECT} известен. [E1]"
    assert result.usage.llm_calls == 2


def test_the_production_graph_records_into_a_substituted_run_store() -> None:
    record = Registry("memory_registry", RECORD)
    store = CountingRunStore()
    store.create("run-1", question=QUESTION, request={})

    result = investigate(record, runs=store, run_id="run-1")

    assert_the_whole_chain_ran(result, record)
    assert result.run_id == "run-1"
    # Saved when planned and after each coverage; each search recorded as it ended,
    # and the start of each follow-up round.
    assert store.calls.count("save_progress") >= 2
    assert store.calls.count("update_progress") == len(record.calls) + result.usage.followup_rounds
    stored = store.load("run-1")
    assert stored is not None and stored.progress is not None
    assert len(stored.progress["done"]) == 2


def test_a_query_goes_only_to_the_retriever_whose_capabilities_answer_it() -> None:
    # A answers who the subject is and nothing else; B answers words for the status.
    people = Registry("retriever_a", RECORD, kinds=frozenset({"person"}), facts=frozenset())
    words = Registry(
        "retriever_b", RECORD, kinds=frozenset({"keywords"}), facts=frozenset({"status"})
    )

    result = investigate(people, words)

    assert [query.kind for query in people.calls] == ["person"]
    assert [(query.kind, query.fact) for query in words.calls] == [("keywords", "status")]
    assert result.status == "completed"
    assert {item.source_id for item in result.evidence} == {"retriever_a", "retriever_b"}


def test_a_status_nobody_holds_ends_the_run_partial_after_one_follow_up() -> None:
    record = Registry("memory_registry", {"identity": RECORD["identity"]})

    result = investigate(record)

    assert result.status == "partial" and result.missing_information == ["status"]
    # The same follow-up would be the same search: it is not made twice.
    assert [query.kind for query in record.calls] == ["person", "keywords"]


class UnreadScenario(SubjectScenario):
    """Registered after the one that matches: it is never asked to read the question."""

    name: ScenarioName = "test_unread"

    def intent_by_rules(self, question: str) -> InvestigationIntent | None:
        raise AssertionError("a later scenario was read after an earlier one matched")


def test_the_first_scenario_that_finds_a_subject_is_taken_without_reading_the_rest() -> None:
    registry = ScenarioRegistry([SubjectScenario(), UnreadScenario()])

    intent = registry.by_rules(QUESTION)

    assert intent.scenario == "test_subject" and intent.subject_name == SUBJECT
