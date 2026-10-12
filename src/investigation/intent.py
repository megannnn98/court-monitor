"""From a question to a structured intent: which scenario, about whom or what, which
facts.

Rules read it first. A model may read it instead, into the same schema; its reading is
taken only if every part passes the checks below, else the rules' reading stands. A
model cannot add a source, a tool or a scenario: the schema has no field for the first
two and an enum for the third, and the sources a run keeps to come from the request.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from investigation.models import SCENARIOS, FactRequirement, InvestigationIntent, ScenarioName
from investigation.services.model import ModelReply, prompt

_WORD = re.compile(r"[^\W\d_]+(?:-[^\W\d_]+)*", re.UNICODE)
# Capitalized words that open a question and are no part of a name.
_NOT_A_NAME = frozenset(
    {
        "найди", "найти", "найдите", "что", "кто", "как", "какие", "какой", "какая", "где",
        "когда", "расскажи", "расскажите", "собери", "соберите", "покажи", "покажите",
        "есть", "известно", "информация", "информацию", "данные", "дело", "дела", "про",
        "по", "на", "об", "обо", "о", "в", "и", "за", "ли", "суд", "россия", "россии",
        "рф", "ук", "росфинмониторинг", "росфинмониторинга", "мемориал", "установи",
        "установить", "определи", "фигурант", "фигуранта", "статья", "статьи",
        "публикация", "публикации", "новость", "новости", "последние",
    }
)  # fmt: skip
_MAX_NAME_WORDS = 3
_MAX_ENDING = 3
_MIN_STEM = 3

# «публикации №12345», «материал 77», «article 12345», «/articles/12345»: a stored
# publication by its id. «статья» is a publication too, but also a Criminal Code article
# («статьи 280 УК», «205.2»): after it an id is a number of at least four digits not
# followed by «УК» or a dotted part.
_PUBLICATION_ID = re.compile(
    r"(?:публикаци[яиеюей]|материал[ауе]?|новост[ьиюей]|article|articles/)"
    r"\s*(?:№|#|id|n)?\s*(\d{1,9})\b",
    re.IGNORECASE,
)
_ARTICLE_ID = re.compile(
    r"стать[яиеюей]\s*(?:№|#|id)?\s*(\d{4,9})\b(?!\s*(?:ук|\.\d))", re.IGNORECASE
)
# «без имени», «неназванный», «установи фигуранта»: who a publication does not name.
_UNNAMED = re.compile(
    r"без\s+имени|неназванн|не\s+назван|безымянн|установи(?:ть)?\s+(?:фигуранта|кто)|"
    r"кто\s+(?:этот|эта|это)\s+(?:фигурант|человек)|unnamed|кто\s+фигурант",
    re.IGNORECASE,
)

# Words of a question that ask for one fact.
_FACT_WORDS: Mapping[str, tuple[str, ...]] = {
    "rf_list": ("перечн", "росфинмониторинг", "террорист", "экстремист"),
    "charges": ("стать", "обвин", "за что"),
    "politics": ("политическ", "преследу"),
    "latest_news": ("последн", "новост", "событи"),
    "role": ("роль", "фигурант", "подозрева"),
}

INTAKE_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "scenario": {"type": ["string", "null"], "enum": [*SCENARIOS, None]},
        "subject_name": {"type": ["string", "null"]},
        "article_id": {"type": ["integer", "null"]},
        "requested_facts": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["scenario", "subject_name", "article_id", "requested_facts"],
    "additionalProperties": False,
}
INTAKE_MAX_TOKENS = 200
MAX_REQUESTED_FACTS = 8


class _Intake(BaseModel):
    # A field the schema does not have — a tool, a URL, a source — refuses the reading.
    model_config = ConfigDict(extra="forbid")

    scenario: ScenarioName | None = None
    subject_name: str | None = None
    article_id: int | None = None
    requested_facts: list[str] = Field(default_factory=list, max_length=MAX_REQUESTED_FACTS)


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


def article_by_rules(question: str) -> int | None:
    match = _PUBLICATION_ID.search(question) or _ARTICLE_ID.search(question)
    return int(match.group(1)) if match else None


def asks_for_unnamed(question: str) -> bool:
    return bool(_UNNAMED.search(question))


def requested_facts(question: str, facts: Mapping[str, FactRequirement]) -> list[str]:
    """The facts the question asks for by its words; empty: it asks for everything."""
    folded = question.lower()
    return [
        key
        for key, words in _FACT_WORDS.items()
        if key in facts and any(word in folded for word in words)
    ]


def _fold(word: str) -> str:
    return word.lower().replace("ё", "е")


def _one_word(first: str, second: str) -> bool:
    """Two forms of one word: they differ in an ending of at most three letters.
    «Сидорове» and «Сидоров» are; «Петренко» and «Петров», «Петровского» and «Петров»
    are not."""
    if first == second:
        return True
    shared = 0
    for left, right in zip(first, second, strict=False):
        if left != right:
            break
        shared += 1
    return shared >= max(_MIN_STEM, max(len(first), len(second)) - _MAX_ENDING)


def named_in(question: str, name: str) -> bool:
    """Every word of the name stands in the question, in any case of the noun."""
    asked = [_fold(word) for word in _WORD.findall(question)]
    words = [_fold(word) for word in _WORD.findall(name)]
    return bool(words) and all(any(_one_word(found, word) for found in asked) for word in words)


def intake_messages(question: str) -> tuple[str, str]:
    """(system, user). The question is data inside its own block."""
    defused = question.replace("</", "<\\/")
    return prompt("intake"), f"<question>\n{defused}\n</question>"


def intent_from_reply(
    question: str,
    reply: ModelReply,
    *,
    facts_of: Mapping[ScenarioName, Mapping[str, FactRequirement]],
) -> tuple[InvestigationIntent | None, str | None]:
    """The model's reading of the question, or None and why it was refused.

    Refused whole when any part fails: an unknown scenario, a name the question does not
    hold, an id the question does not hold, a fact the scenario does not have."""
    try:
        read = _Intake.model_validate(json.loads(reply.text))
    except (ValueError, ValidationError):
        return None, "ответ модели не по схеме"
    name = (read.subject_name or "").strip() or None
    if name is not None:
        if not named_in(question, name):
            return None, "модель назвала имя, которого нет в вопросе"
        name = " ".join(name.split()[:_MAX_NAME_WORDS])
    if read.article_id is not None and str(read.article_id) not in re.findall(r"\d+", question):
        return None, "модель назвала публикацию, которой нет в вопросе"
    scenario = read.scenario
    if scenario is None:
        # No scenario named: a publication is the unnamed one's, a name is a dossier.
        if read.article_id is not None:
            scenario = "identify_unnamed"
        elif name is not None:
            scenario = "person_dossier"
        else:
            return None, None
    known = facts_of.get(scenario, {})
    unknown = [fact for fact in read.requested_facts if fact not in known]
    if unknown:
        return None, "модель запросила неизвестные факты: " + ", ".join(unknown[:3])
    if scenario == "person_dossier" and name is None:
        return None, None
    if scenario == "identify_unnamed" and read.article_id is None:
        return None, None
    return (
        InvestigationIntent(
            scenario=scenario,
            subject_name=name,
            article_id=read.article_id,
            requested_facts=list(dict.fromkeys(read.requested_facts)),
            read_by="model",
        ),
        None,
    )


def with_sources(intent: InvestigationIntent, sources: Sequence[str]) -> InvestigationIntent:
    return intent.model_copy(update={"source_constraints": list(sources)})
