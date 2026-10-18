"""Stored question history for the operator's answer pages."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from results.storage.models import ChatQuestionRecord, MonitoringFindingRecord


@dataclass(frozen=True)
class QuestionAnswer:
    id: int
    asked_at: datetime
    question: str
    calls: list[Any]
    answer: str
    outcome: str
    model: str
    cost_usd: float


def _answer(record: ChatQuestionRecord) -> QuestionAnswer:
    return QuestionAnswer(
        id=record.id,
        asked_at=record.asked_at,
        question=record.question,
        calls=deepcopy(record.calls),
        answer=record.answer,
        outcome=record.outcome,
        model=record.model,
        cost_usd=record.cost_usd,
    )


def question_answer(session: Session, id_: int) -> QuestionAnswer | None:
    """A previously answered question; None for an unknown id."""
    record = session.get(ChatQuestionRecord, id_)
    return None if record is None else _answer(record)


def recent_answers(session: Session, *, limit: int) -> list[QuestionAnswer]:
    """Latest questions, newest first, with the evidence that supports their answers."""
    return [
        _answer(record)
        for record in session.scalars(
            select(ChatQuestionRecord).order_by(ChatQuestionRecord.id.desc()).limit(limit)
        )
    ]


def active_finding_count(session: Session) -> int:
    """How many monitoring findings are active."""
    return int(
        session.scalar(
            select(func.count(MonitoringFindingRecord.id)).where(
                MonitoringFindingRecord.active.is_(True)
            )
        )
        or 0
    )
