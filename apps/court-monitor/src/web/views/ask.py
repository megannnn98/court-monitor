"""«Спросить»: a question in plain words, the answer, and the counts it stands on.

`results.ask` does the work. The page shows the answer with every count the model
called for under it, so a number is checked by eye; a number of the answer that no count
holds is named above it. The questions asked before are listed below.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from results.ask import (
    FAILED,
    REFUSED,
    asker_from_env,
    unverified_numbers,
)
from results.read import QuestionAnswer, recent_answers

HISTORY = 20

EXAMPLES = (
    "В каких регионах самые суровые наказания за антивоенные высказывания?",
    "Сколько приговоров за госизмену было в 2026 году и какие сроки давали?",
    "Какие самые большие сроки давали заочно?",
    "Что известно о задержаниях на одиночных пикетах?",
)


def _number(value: object) -> str:
    return f"{value:g}".replace(".", ",") if isinstance(value, float) else str(value)


def results_of(record: QuestionAnswer) -> list[dict[str, Any]]:
    """The counts the answer stands on, as the model's calls returned them."""
    return [call["result"] for call in record.calls]


def unverified_of(record: QuestionAnswer) -> list[str]:
    """The answer's numbers no count holds; none for a refusal or a failure."""
    if record.outcome in (REFUSED, FAILED):
        return []
    return unverified_numbers(record.answer, record.question, results_of(record))


def current_asker() -> Any:
    """The model to ask, as the environment names it; looked up on every question."""
    return asker_from_env()


def asked_before(db: Session) -> list[QuestionAnswer]:
    """The latest questions, newest first."""
    return recent_answers(db, limit=HISTORY)
