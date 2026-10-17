"""The nodes of the graph. Each takes the state and what it may call (`Toolbox`), and
returns only what it adds to the state. No node knows a scenario's facts: it asks the
scenario of the run.

    intake          the question as a structured intent; which scenario
    plan            the first searches: each query to the retrievers able to answer it
    search          one query to one retriever; many of these run in parallel
    merge_evidence  no repeats, ranked, a ceiling, numbers to cite; the subject settled
    coverage        known, missing, conflicting; the next round's searches, gaps only
    follow_up       the next round
    report          findings with their evidence, the status, an optional summary
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ValidationError

from investigation import intent as intent_rules
from investigation import planner, telemetry
from investigation.models import (
    EvidenceItem,
    InvestigationIntent,
    InvestigationPlan,
    InvestigationResult,
    Limits,
    SearchTask,
    Subject,
    Usage,
)
from investigation.runs import RunStore
from investigation.scenarios.base import InvestigationScenario, ScenarioView
from investigation.scenarios.registry import ScenarioRegistry
from investigation.services import evidence as evidence_rules
from investigation.services.model import InvestigationModel, ModelReply, prompt
from investigation.services.retrieval import InvestigationRetriever, RetrieverRegistry
from investigation.state import InvestigationState, SearchBranch, dump_state
from llm.chat import ModelError

NO_SUBJECT = "в вопросе не названо имя человека"
NO_ARTICLE = "в вопросе не указана публикация с неназванным фигурантом"
SUMMARY_UNCHECKED = (
    "сводка написана моделью и не проверена: у каждой фразы есть ссылка на существующий "
    "материал, но не проверено, что материал говорит именно это"
)
SUMMARY_OUT_OF_TIME = "время исследования истекло: сводка моделью не составлена"
FOLLOWUPS_LEFT = "лимит дополнительных поисков исчерпан: недостающие данные искали не до конца"
EXHAUSTED = "новых способов поиска недостающих данных не осталось: искали везде, где могли"
NO_RETRIEVER = "ни один разрешённый источник не умеет искать то, что нужно"
REPORT_MAX_TOKENS = 1_500
# Characters of one piece of evidence shown to the model.
EVIDENCE_SHOWN = 600
# The keys of the state a stored run is resumed from.
RESUMABLE = (
    "question",
    "limits",
    "local_only",
    "sources",
    "requested_scenario",
    "intent",
    "subject_ref",
    "subject",
    "plan",
    "tasks",
    "done",
    "answered",
    "found",
    "usage",
    "warnings",
)

REPORT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "sentences": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "evidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["sentences"],
    "additionalProperties": False,
}


class NotEvidenceError(TypeError):
    """A retriever returned something that is not an `EvidenceItem`."""


@dataclass(frozen=True)
class Toolbox:
    """What the nodes may call: the retrievers and scenarios of the service, the
    optional model and the optional store of runs. Built once; a run adds nothing."""

    retrievers: RetrieverRegistry
    scenarios: ScenarioRegistry
    model: InvestigationModel | None = None
    runs: RunStore | None = None


def _limits(state: InvestigationState) -> Limits:
    return state.get("limits") or Limits()


def _usage(state: InvestigationState) -> Usage:
    return state.get("usage") or Usage()


def _spent(reply: ModelReply) -> Usage:
    return Usage(
        llm_calls=1,
        input_tokens=reply.input_tokens,
        output_tokens=reply.output_tokens,
        cost_usd=reply.cost_usd,
    )


def _may_ask(state: InvestigationState, tools: Toolbox) -> bool:
    return tools.model is not None and _usage(state).llm_calls < _limits(state).max_llm_calls


def scenario_of(state: InvestigationState, tools: Toolbox) -> InvestigationScenario:
    intent = state.get("intent")
    assert intent is not None
    return tools.scenarios.get(intent.scenario)


def allowed(state: InvestigationState, tools: Toolbox) -> list[InvestigationRetriever]:
    return tools.retrievers.allowed(
        local_only=state.get("local_only", False), sources=state.get("sources", [])
    )


def _view(state: InvestigationState, *, subject: Subject | None = None) -> ScenarioView:
    intent = state.get("intent")
    subject_ref = state.get("subject_ref")
    assert intent is not None and subject_ref is not None
    return ScenarioView(
        intent=intent,
        subject_ref=subject_ref,
        subject=subject if subject is not None else state.get("subject"),
        evidence=state.get("evidence", []),
        found=state.get("found", []),
        rounds=_usage(state).followup_rounds,
        answered=state.get("answered", []),
    )


async def _ask(
    tools: Toolbox,
    *,
    system: str,
    user: str,
    schema_name: str,
    schema: dict[str, object],
    max_tokens: int,
) -> ModelReply:
    assert tools.model is not None
    return await asyncio.to_thread(
        tools.model.ask,
        system=system,
        user=user,
        schema_name=schema_name,
        schema=schema,
        max_tokens=max_tokens,
    )


async def _store(tools: Toolbox, method: str, *args: Any, **kwargs: Any) -> None:
    """A call of the run store; a store that fails costs the record, not the run."""
    if tools.runs is None:
        return
    try:
        await asyncio.to_thread(getattr(tools.runs, method), *args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - a lost record is logged, the run goes on
        telemetry.logger.warning(
            "event=investigation_store_failed method=%s error=%s", method, type(exc).__name__
        )


async def intake(state: InvestigationState, tools: Toolbox) -> dict[str, Any]:
    question = state["question"]
    requested = state.get("requested_scenario")
    update: dict[str, Any] = {}
    intent: InvestigationIntent | None = None
    if _may_ask(state, tools):
        system, user = intent_rules.intake_messages(question)
        try:
            reply = await _ask(
                tools,
                system=system,
                user=user,
                schema_name="investigation_intake",
                schema=intent_rules.intake_schema(list(tools.scenarios.facts)),
                max_tokens=intent_rules.INTAKE_MAX_TOKENS,
            )
        except ModelError as exc:
            update["warnings"] = [f"модель не разобрала вопрос ({exc}); имя взято по правилам"]
            update["usage"] = Usage(llm_calls=1)
        else:
            update["usage"] = _spent(reply)
            intent, refused = intent_rules.intent_from_reply(
                question, reply, facts_of=tools.scenarios.facts
            )
            if intent is not None and requested is not None and intent.scenario != requested:
                intent, refused = None, "модель выбрала не тот сценарий, что задан запросом"
            if refused:
                update["warnings"] = [f"разбор модели отклонён: {refused}; взят разбор по правилам"]
    if intent is None:
        intent = tools.scenarios.by_rules(question, scenario=requested)
    intent = intent_rules.with_sources(intent, state.get("sources", []))
    scenario = tools.scenarios.get(intent.scenario)
    subject_ref = scenario.subject_of(intent)
    update["intent"] = intent
    update["subject_ref"] = subject_ref
    update["subject"] = None if subject_ref is None else Subject(name=scenario.subject_name(intent))
    return update


async def plan(state: InvestigationState, tools: Toolbox) -> dict[str, Any]:
    scenario = scenario_of(state, tools)
    intent = state.get("intent")
    subject_ref = state.get("subject_ref")
    assert intent is not None and subject_ref is not None
    tasks = planner.assign(
        scenario.initial_queries(intent, subject_ref),
        allowed(state, tools),
        round_=0,
        done=set(state.get("done", [])),
        facts=scenario.facts,
    )
    kept, cut = planner.cap(tasks, _limits(state).max_searches - _usage(state).search_calls)
    plan_ = InvestigationPlan(
        scenario=scenario.name,
        subject=subject_ref,
        requirements=list(scenario.facts.values()),
        tasks=kept,
    )
    telemetry.emit(
        "plan_built",
        run_id=state.get("run_id"),
        scenario=scenario.name,
        tasks=len(kept),
        retrievers=",".join(sorted({task.retriever for task in kept})) or "-",
    )
    warnings = [] if kept else [NO_RETRIEVER]
    if cut:
        warnings.append(f"лимит поисков: {cut} поисков первого раунда не выполнено")
    if state.get("run_id"):
        await _store(
            tools,
            "save_progress",
            state.get("run_id"),
            scenario=scenario.name,
            round_=0,
            usage=_usage(state).model_dump(mode="json"),
            progress=dump_state(
                {
                    **{key: state[key] for key in RESUMABLE if key in state},  # type: ignore[literal-required]
                    "plan": plan_,
                    "tasks": kept,
                }
            ),
        )
    return {"plan": plan_, "tasks": kept, "warnings": warnings}


async def _record(tools: Toolbox, run_id: str | None, update: dict[str, Any]) -> dict[str, Any]:
    """What one search adds to the state, written to the stored run as the search ends:
    a run stopped in the middle of a round keeps the searches that were made, and a
    resumed one does not make them again. Returns `update` as it was given."""
    if run_id is None or tools.runs is None:
        return update
    done = list(update.get("done", []))
    found = dump_state({"found": update.get("found", [])})["found"]
    answered = dump_state({"answered": update.get("answered", [])})["answered"]
    warnings = list(update.get("warnings", []))
    spent = update.get("usage")

    def change(progress: dict[str, Any]) -> dict[str, Any]:
        usage = Usage.model_validate(progress.get("usage") or {})
        return {
            **progress,
            "done": [*progress.get("done", []), *done],
            "found": [*progress.get("found", []), *found],
            "answered": [*progress.get("answered", []), *answered],
            "warnings": [*progress.get("warnings", []), *warnings],
            "usage": (usage + spent if isinstance(spent, Usage) else usage).model_dump(mode="json"),
        }

    await _store(tools, "update_progress", run_id, change)
    return update


def _stamped(items: Sequence[EvidenceItem], task: SearchTask) -> list[EvidenceItem]:
    now = datetime.now(UTC)
    return [
        item.model_copy(
            update={
                "query": item.query or task.query.text,
                "round": task.round,
                "retrieved_at": item.retrieved_at or now,
            }
        )
        for item in items
    ]


async def search(
    branch: SearchBranch,
    tools: Toolbox,
    slots: asyncio.Semaphore | None,
    unfinished: set[asyncio.Future[Any]] | None = None,
) -> dict[str, Any]:
    """One query to one retriever, within its time and a free slot. A retriever that
    fails or runs out of time costs its own evidence and a warning, never the run."""
    task = branch["task"]
    retriever = tools.retrievers.get(task.retriever)
    network = retriever.capabilities.network
    spent = Usage(
        search_calls=1,
        network_searches=1 if network else 0,
        local_searches=0 if network else 1,
    )
    fields: dict[str, object] = {
        "run_id": branch.get("run_id"),
        "scenario": branch.get("scenario"),
        "retriever": retriever.name,
        "round": task.round,
        "query_hash": telemetry.query_hash(task.query.text),
        "query_length": len(task.query.text),
    }
    run_id = branch.get("run_id")
    timeout = min(branch["search_timeout"], branch["deadline"] - time.time())
    if timeout <= 0:
        return await _record(
            tools,
            run_id,
            {
                "done": [task.fingerprint],
                "warnings": [f"время исследования истекло: источник «{retriever.name}» не спрошен"],
            },
        )
    started = time.perf_counter()
    telemetry.emit("search_started", context=fields)

    try:
        found = await _within_a_slot(retriever, task, branch, slots, unfinished)
    except TimeoutError:
        telemetry.emit(
            "search_failed",
            duration_ms=(time.perf_counter() - started) * 1000,
            failure="timeout",
            context=fields,
        )
        return await _record(
            tools,
            run_id,
            {
                "done": [task.fingerprint],
                "usage": spent + Usage(failed_searches=1),
                "warnings": [f"источник «{retriever.name}» не ответил вовремя"],
            },
        )
    except Exception as exc:  # noqa: BLE001 - one source's failure is its own warning
        telemetry.emit(
            "search_failed",
            duration_ms=(time.perf_counter() - started) * 1000,
            failure=type(exc).__name__,
            context=fields,
        )
        return await _record(
            tools,
            run_id,
            {
                "done": [task.fingerprint],
                "usage": spent + Usage(failed_searches=1),
                "warnings": [f"источник «{retriever.name}» не ответил: {type(exc).__name__}"],
            },
        )
    if not all(isinstance(item, EvidenceItem) for item in found):
        # A retriever that answers with something else is broken, not unavailable: the
        # run fails rather than report on what it could not read.
        raise NotEvidenceError(
            f"retriever {retriever.name} returned something that is not evidence"
        )
    telemetry.emit(
        "search_completed",
        duration_ms=(time.perf_counter() - started) * 1000,
        results=len(found),
        context=fields,
    )
    return await _record(
        tools,
        run_id,
        {
            "found": _stamped(found, task),
            "done": [task.fingerprint],
            "answered": [task.query],
            "usage": spent,
        },
    )


async def _within_a_slot(
    retriever: InvestigationRetriever,
    task: SearchTask,
    branch: SearchBranch,
    slots: asyncio.Semaphore | None,
    unfinished: set[asyncio.Future[Any]] | None = None,
) -> list[EvidenceItem]:
    """The search's answer, within its time and one of the run's free slots.

    A local retriever works in a thread, and a thread cannot be stopped: when its time
    is up the wait ends, the work does not. Its slot therefore stays taken until the
    work really ends — otherwise each timeout would let one more search run beside it,
    past the bound on parallel searches. (Its SQL is cut by the database at the search
    timeout, so that is soon.) A network search is cancelled, and its slot freed at once.

    The same holds when the whole run is cancelled: a local search left working is put
    in `unfinished`, and the run is not let go to another process until it has ended.
    """
    if slots is not None:
        await slots.acquire()
    held = slots is not None

    def free(_: object = None) -> None:
        nonlocal held
        if held and slots is not None:
            held = False
            slots.release()

    try:
        # The time left is measured once a slot is free: a search that waited for one
        # still ends by the run's deadline.
        left = min(branch["search_timeout"], branch["deadline"] - time.time())
        if left <= 0:
            raise TimeoutError
        work = asyncio.ensure_future(retriever.search(task.query))
    except BaseException:
        free()
        raise

    def abandon() -> None:
        """Nobody waits for the work any more. A network search is cancelled; a local
        one cannot be, and keeps its slot and its place among the unfinished."""
        if retriever.capabilities.network:
            work.cancel()
        elif unfinished is not None:
            unfinished.add(work)
            work.add_done_callback(unfinished.discard)
        # Whatever it ends with now is nobody's: not a «task exception never retrieved».
        work.add_done_callback(lambda finished: finished.cancelled() or finished.exception())
        work.add_done_callback(free)

    try:
        done, _pending = await asyncio.wait({work}, timeout=left)
    except BaseException:
        # The run itself was cancelled.
        abandon()
        raise
    if done:
        free()
        return work.result()
    abandon()
    raise TimeoutError


async def merge_evidence(state: InvestigationState, tools: Toolbox) -> dict[str, Any]:
    merged, cut = evidence_rules.merge(
        state.get("found", []), limit=_limits(state).max_evidence_items
    )
    scenario = scenario_of(state, tools)
    subject = scenario.settle(_view({**state, "evidence": merged}))
    update: dict[str, Any] = {"evidence": merged, "subject": subject}
    if cut:
        update["warnings"] = [f"найдено больше материалов, чем разрешено показать: {cut} отброшено"]
    return update


async def coverage(state: InvestigationState, tools: Toolbox) -> dict[str, Any]:
    scenario = scenario_of(state, tools)
    view = _view(state)
    assessment = scenario.assess(view)
    done = set(state.get("done", []))
    tasks = planner.assign(
        planner.planned(assessment.follow_up_queries),
        allowed(state, tools),
        round_=view.rounds + 1,
        done=done,
        facts=scenario.facts,
    )
    # Something is missing and has no lead, a follow-up looked or wanted to look for it,
    # and no search is left that was not made already. A gap with a lead ended its search
    # by finding something; it is not exhausted.
    gaps = assessment.missing if assessment.open_gaps is None else assessment.open_gaps
    exhausted = bool(gaps) and not tasks and (bool(assessment.follow_up_queries) or view.rounds > 0)
    assessment = assessment.model_copy(
        update={"follow_up_tasks": tasks, "enough": not tasks, "exhausted": exhausted}
    )
    telemetry.emit(
        "coverage_assessed",
        run_id=state.get("run_id"),
        scenario=scenario.name,
        round=view.rounds,
        known=len(assessment.known),
        missing=len(assessment.missing),
        conflicts=len(assessment.conflict_details),
        follow_up_tasks=len(tasks),
    )
    if state.get("run_id"):
        await _store(
            tools,
            "save_progress",
            state.get("run_id"),
            scenario=scenario.name,
            round_=view.rounds,
            usage=_usage(state).model_dump(mode="json"),
            progress=dump_state({key: state[key] for key in RESUMABLE if key in state}),  # type: ignore[literal-required]
        )
    return {"coverage": assessment}


async def follow_up(state: InvestigationState, tools: Toolbox) -> dict[str, Any]:
    assessment = state.get("coverage")
    assert assessment is not None
    remaining = _limits(state).max_searches - _usage(state).search_calls
    kept, cut = planner.cap(assessment.follow_up_tasks, remaining)
    telemetry.emit(
        "followup_started",
        run_id=state.get("run_id"),
        round=_usage(state).followup_rounds + 1,
        tasks=len(kept),
    )
    update: dict[str, Any] = {"tasks": kept, "usage": Usage(followup_rounds=1)}
    if cut:
        update["warnings"] = [f"лимит поисков: {cut} дополнительных поисков не выполнено"]
    if state.get("run_id") and tools.runs is not None:
        # The round is stored as it starts: its number and its searches. A run stopped
        # after them and before their coverage would otherwise resume a round behind,
        # plan the same searches, find them made and end without the next round.
        tasks = dump_state({"tasks": kept})["tasks"]
        warnings = list(update.get("warnings", []))

        def started(progress: dict[str, Any]) -> dict[str, Any]:
            usage = Usage.model_validate(progress.get("usage") or {}) + Usage(followup_rounds=1)
            return {
                **progress,
                "tasks": tasks,
                "usage": usage.model_dump(mode="json"),
                "warnings": [*progress.get("warnings", []), *warnings],
            }

        await _store(tools, "update_progress", state.get("run_id"), started)
    return update


class _Sentence(BaseModel):
    text: str
    evidence: list[str]


class _Report(BaseModel):
    sentences: list[_Sentence]


def report_messages(question: str, evidence: Sequence[EvidenceItem]) -> tuple[str, str]:
    """(system, user). The question and the evidence stand in separate blocks; a closing
    tag written inside a quote is defused, so a quote cannot end its own block."""

    def shown(text: str) -> str:
        return text[:EVIDENCE_SHOWN].replace("</", "<\\/")

    listed = "\n".join(f"[{item.ref}] {shown(item.text)}" for item in evidence)
    return (
        prompt("report"),
        f"<question>\n{shown(question)}\n</question>\n<evidence>\n{listed}\n</evidence>",
    )


def supported_summary(
    reply: ModelReply, evidence: Sequence[EvidenceItem]
) -> tuple[str | None, int]:
    """The model's sentences that cite existing evidence, joined; and how many were
    dropped for citing none. None when the answer is unreadable or nothing is left.

    This checks that a citation exists, not that the evidence supports the sentence: a
    model can cite «E1» under anything. So the summary is never a source of a fact."""
    known = {item.ref for item in evidence}
    try:
        sentences = _Report.model_validate(json.loads(reply.text)).sentences
    except (ValueError, ValidationError):
        return None, 0
    kept = [
        f"{sentence.text.strip()} [{', '.join(sentence.evidence)}]"
        for sentence in sentences
        if sentence.text.strip() and sentence.evidence and set(sentence.evidence) <= known
    ]
    return (" ".join(kept) or None), len(sentences) - len(kept)


async def report(state: InvestigationState, tools: Toolbox) -> dict[str, Any]:
    question = state["question"]
    intent = state.get("intent")
    subject_ref = state.get("subject_ref")
    subject = state.get("subject")
    evidence = state.get("evidence", [])
    assessment = state.get("coverage")
    warnings: list[str] = []
    spent = Usage()
    summary: str | None = None
    scenario = None if intent is None else tools.scenarios.get(intent.scenario)

    if scenario is None or subject_ref is None or intent is None:
        missing = [NO_ARTICLE if intent and intent.scenario == "identify_unnamed" else NO_SUBJECT]
        conflicts: list[str] = []
        conflict_details = []
        findings = []
        candidates = []
        status = "clarification_required"
        subject = None
    else:
        assert assessment is not None
        view = _view(state)
        missing = list(assessment.missing)
        conflicts = list(assessment.conflicts)
        conflict_details = list(assessment.conflict_details)
        findings = scenario.findings(view)
        candidates = scenario.candidates(view)
        status = scenario.status(view, assessment)
        warnings.extend(assessment.warnings)
        if assessment.follow_up_tasks:
            warnings.append(FOLLOWUPS_LEFT)
        elif assessment.exhausted:
            warnings.append(EXHAUSTED)
        cited = ", ".join(item.ref for item in evidence if item.leads)
        if cited:
            warnings.append(
                "по недостающим данным найдены цитаты в публикациях, не проверены: " + cited
            )
        if time.time() > state.get("deadline", float("inf")):
            warnings.append("время исследования истекло: результат собран из того, что успели")

    if evidence and tools.model is not None:
        # The summary is the one optional step: it never starts after the deadline and
        # never outlives it. What the run found stands with it or without.
        left = state.get("deadline", float("inf")) - time.time()
        if left <= 0:
            warnings.append(SUMMARY_OUT_OF_TIME)
        elif _may_ask(state, tools):
            system, user = report_messages(question, evidence)
            try:
                reply = await asyncio.wait_for(
                    _ask(
                        tools,
                        system=system,
                        user=user,
                        schema_name="investigation_report",
                        schema=REPORT_SCHEMA,
                        max_tokens=REPORT_MAX_TOKENS,
                    ),
                    None if left == float("inf") else left,
                )
            except TimeoutError:
                spent = Usage(llm_calls=1)
                warnings.append(SUMMARY_OUT_OF_TIME)
            except ModelError as exc:
                spent = Usage(llm_calls=1)
                warnings.append(f"модель не составила сводку: {exc}")
            else:
                spent = _spent(reply)
                summary, dropped = supported_summary(reply, evidence)
                if summary is not None:
                    warnings.append(SUMMARY_UNCHECKED)
                if dropped:
                    warnings.append(
                        f"{dropped} утверждений модели отброшены: они не ссылаются на материалы"
                    )
        else:
            warnings.append("лимит обращений к модели исчерпан: сводка не составлена")

    started = state.get("started_at")
    usage = _usage(state) + spent
    usage = usage.model_copy(
        update={
            "evidence_items": len(evidence),
            "elapsed_ms": int((time.time() - started) * 1000) if started else 0,
        }
    )
    result = InvestigationResult(
        status=status,  # type: ignore[arg-type]
        question=question,
        run_id=state.get("run_id"),
        scenario=None if intent is None else intent.scenario,
        intent=intent,
        plan=state.get("plan"),
        subject=subject,
        findings=findings,
        evidence=list(evidence),
        missing_information=missing,
        conflicts=conflicts,
        conflict_details=conflict_details,
        candidates=candidates,
        warnings=list(dict.fromkeys([*state.get("warnings", []), *warnings])),
        model_summary=summary,
        usage=usage,
    )
    return {"result": result, "usage": spent, "warnings": warnings}
