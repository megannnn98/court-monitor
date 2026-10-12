"""From a question to a plan: who is asked about, and what to search first.

The plan of the first use case is fixed: one person, looked up in every retriever at
once. A model may read the name out of the question; it may not supply one. The name it
returns is taken only if the question itself holds it.
"""

from __future__ import annotations

import json
import re

from pydantic import BaseModel, ValidationError

from investigation.models import InvestigationQuery
from investigation.services.model import ModelReply, prompt

_WORD = re.compile(r"[^\W\d_]+(?:-[^\W\d_]+)*", re.UNICODE)
# Capitalized words that open a question and are no part of a name.
_NOT_A_NAME = frozenset(
    {
        "найди", "найти", "найдите", "что", "кто", "как", "какие", "какой", "какая", "где",
        "когда", "расскажи", "расскажите", "собери", "соберите", "покажи", "покажите",
        "есть", "известно", "информация", "информацию", "данные", "дело", "дела", "про",
        "по", "на", "об", "обо", "о", "в", "и", "за", "ли", "суд", "россия", "россии",
        "рф", "ук", "росфинмониторинг", "росфинмониторинга", "мемориал",
    }
)  # fmt: skip
_MAX_NAME_WORDS = 3
_STEM_ENDING = 2
_MIN_STEM = 3

INTAKE_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"subject_name": {"type": ["string", "null"]}},
    "required": ["subject_name"],
    "additionalProperties": False,
}
INTAKE_MAX_TOKENS = 200


class _Intake(BaseModel):
    subject_name: str | None


def subject_by_rules(question: str) -> str | None:
    """The longest run of capitalized words that are not question words: «Найди, что
    известно про Ивана Петровича Сидорова» → «Ивана Петровича Сидорова»."""
    runs: list[list[str]] = [[]]
    for word in _WORD.findall(question):
        if word[0].isupper() and len(word) > 1 and word.lower() not in _NOT_A_NAME:
            runs[-1].append(word)
        else:
            runs.append([])
    best = max(runs, key=len)
    return " ".join(best[:_MAX_NAME_WORDS]) if best else None


def _stem(word: str) -> str:
    word = word.lower().replace("ё", "е")
    return word[: max(_MIN_STEM, len(word) - _STEM_ENDING)]


def named_in(question: str, name: str) -> bool:
    """Every word of the name stands in the question, in any case of the noun."""
    asked = [_stem(word) for word in _WORD.findall(question)]
    words = _WORD.findall(name)
    return bool(words) and all(
        any(found.startswith(_stem(word)) or _stem(word).startswith(found) for found in asked)
        for word in words
    )


def intake_messages(question: str) -> tuple[str, str]:
    """(system, user). The question is data inside its own block."""
    return prompt("intake"), f"<question>\n{question}\n</question>"


def subject_from_reply(question: str, reply: ModelReply) -> str | None:
    """The name the model read, or None when it gave none, gave something unreadable,
    or named somebody the question does not hold."""
    try:
        name = _Intake.model_validate(json.loads(reply.text)).subject_name
    except (ValueError, ValidationError):
        return None
    if name is None or not name.strip() or not named_in(question, name):
        return None
    return " ".join(name.split()[:_MAX_NAME_WORDS])


def first_queries(name: str) -> list[InvestigationQuery]:
    return [InvestigationQuery(kind="person", name=name)]
