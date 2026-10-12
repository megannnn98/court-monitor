"""The nodes of the graph. Each takes the state and what it may call (`Toolbox`), and
returns only what it adds to the state.

    intake          who is asked about
    plan            the first queries
    search          one query to one retriever; many of these run in parallel
    merge_evidence  no repeats, a ceiling, numbers to cite; the subject is settled
    coverage        what is known, missing, contradictory; what to look for next
    follow_up       the next round's queries
    report          findings with their evidence, the status, an optional summary
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from investigation import planner
from investigation.models import (
    EvidenceItem,
    Finding,
    InvestigationResult,
    Limits,
    Status,
    Subject,
    Usage,
)
from investigation.services import coverage as coverage_rules
from investigation.services import evidence as evidence_rules
from investigation.services.model import InvestigationModel, ModelReply, prompt
from investigation.services.retrieval import InvestigationRetriever
from investigation.state import InvestigationState, SearchTask
from llm.chat import ModelError

logger = logging.getLogger("investigation")

NO_SUBJECT = "в вопросе не названо имя человека"
SUMMARY_UNCHECKED = (
    "сводка написана моделью и не проверена: у каждой фразы есть ссылка на существующий "
    "материал, но не проверено, что материал говорит именно это"
)
REPORT_MAX_TOKENS = 1_500
# Characters of one piece of evidence shown to the model.
EVIDENCE_SHOWN = 600

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


@dataclass(frozen=True)
class Toolbox:
    """What the nodes may call: the retrievers by name, and the optional model."""

    retrievers: dict[str, InvestigationRetriever]
    model: InvestigationModel | None = None


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


async def intake(state: InvestigationState, tools: Toolbox) -> dict[str, Any]:
    question = state["question"]
    update: dict[str, Any] = {}
    name: str | None = None
    if _may_ask(state, tools):
        system, user = planner.intake_messages(question)
        try:
            reply = await _ask(
                tools,
                system=system,
                user=user,
                schema_name="investigation_intake",
                schema=planner.INTAKE_SCHEMA,
                max_tokens=planner.INTAKE_MAX_TOKENS,
            )
        except ModelError as exc:
            update["warnings"] = [f"модель не разобрала вопрос ({exc}); имя взято по правилам"]
            update["usage"] = Usage(llm_calls=1)
        else:
            update["usage"] = _spent(reply)
            name = planner.subject_from_reply(question, reply)
    if name is None:
        name = planner.subject_by_rules(question)
    update["subject"] = None if name is None else Subject(name=name)
    return update


async def plan(state: InvestigationState, tools: Toolbox) -> dict[str, Any]:
    subject = state.get("subject")
    assert subject is not None
    return {"queries": planner.first_queries(subject.name)}


async def search(task: SearchTask, tools: Toolbox) -> dict[str, Any]:
    """One query to one retriever. A retriever that fails costs its own evidence and a
    warning, never the investigation."""
    retriever = tools.retrievers[task["retriever"]]
    try:
        found = await retriever.search(task["query"])
    except Exception as exc:
        logger.exception("event=investigation_search_failed retriever=%s", retriever.name)
        return {
            "usage": Usage(search_calls=1),
            "warnings": [f"источник «{retriever.name}» не ответил: {type(exc).__name__}"],
        }
    return {"found": found, "usage": Usage(search_calls=1)}


async def merge_evidence(state: InvestigationState, tools: Toolbox) -> dict[str, Any]:
    subject = state.get("subject")
    assert subject is not None
    merged, cut = evidence_rules.merge(
        state.get("found", []), limit=_limits(state).max_evidence_items
    )
    update: dict[str, Any] = {
        "evidence": merged,
        # On everything found: a ceiling on what is shown must not turn several people
        # answering the name into one.
        "subject": evidence_rules.resolve_subject(subject.name, state.get("found", [])),
    }
    if cut:
        update["warnings"] = [f"найдено больше материалов, чем разрешено показать: {cut} отброшено"]
    return update


async def coverage(state: InvestigationState, tools: Toolbox) -> dict[str, Any]:
    subject = state.get("subject")
    assert subject is not None
    return {
        "coverage": coverage_rules.assess(
            subject, state.get("evidence", []), followup_rounds=_usage(state).followup_rounds
        )
    }


async def follow_up(state: InvestigationState, tools: Toolbox) -> dict[str, Any]:
    assessment = state.get("coverage")
    assert assessment is not None
    return {"queries": assessment.follow_up_queries, "usage": Usage(followup_rounds=1)}


def _findings(evidence: Sequence[EvidenceItem]) -> list[Finding]:
    """One finding per fact the pipeline decided, citing every piece that states it."""
    refs: dict[tuple[str, str], list[str]] = {}
    for item in evidence:
        if item.fact is None or item.fact == "identity" or item.value is None:
            continue
        if coverage_rules.decided(item):
            refs.setdefault((item.fact, item.value), []).append(item.ref)
    return [
        Finding(
            fact=fact,
            statement=f"{coverage_rules.FACT_LABELS.get(fact, fact)}: {value}",
            evidence=cited,
        )
        for (fact, value), cited in refs.items()
    ]


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


def _status(subject: Subject | None, missing: Sequence[str], conflicts: Sequence[str]) -> Status:
    """From the facts alone: what a model wrote, or failed to write, never moves it."""
    if subject is None:
        return "clarification_required"
    if conflicts:
        return "review_required"
    if subject.entity_key is None or missing:
        return "partial"
    return "completed"


async def report(state: InvestigationState, tools: Toolbox) -> dict[str, Any]:
    question = state["question"]
    subject = state.get("subject")
    evidence = state.get("evidence", [])
    assessment = state.get("coverage")
    warnings: list[str] = []
    spent = Usage()
    summary: str | None = None
    dropped = 0

    if subject is None:
        missing, conflicts = [NO_SUBJECT], []
    else:
        missing = list(assessment.missing) if assessment else []
        conflicts = list(assessment.conflicts) if assessment else []
        if assessment is not None and not assessment.enough:
            warnings.append(
                "лимит дополнительных поисков исчерпан: недостающие данные искали не до конца"
            )
        cited = ", ".join(item.ref for item in evidence if item.leads)
        if cited:
            warnings.append(
                "по недостающим данным найдены цитаты в публикациях, не проверены: " + cited
            )

    if evidence and tools.model is not None:
        if _may_ask(state, tools):
            system, user = report_messages(question, evidence)
            try:
                reply = await _ask(
                    tools,
                    system=system,
                    user=user,
                    schema_name="investigation_report",
                    schema=REPORT_SCHEMA,
                    max_tokens=REPORT_MAX_TOKENS,
                )
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

    result = InvestigationResult(
        status=_status(subject, missing, conflicts),
        question=question,
        subject=subject,
        findings=_findings(evidence),
        evidence=list(evidence),
        missing_information=missing,
        conflicts=conflicts,
        warnings=[*state.get("warnings", []), *warnings],
        model_summary=summary,
        usage=_usage(state) + spent,
    )
    return {"result": result, "usage": spent, "warnings": warnings}
