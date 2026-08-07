"""Asking a model whether a candidate's person is the one in the registry.

This exists because the score cannot answer that question and never will: news
text carries no birth date or place (measured — 5 and 0 out of 379 documents),
so every candidate matches on the name alone and lands on the same 0.50. The
model reads the document instead and says whether the person described there is
consistent with the registry record, quoting the text it based that on.

**It does not decide.** The verdict is an inferred fact for the operator, in the
same sense as every regex-extracted fact: it never touches ``status`` and never
touches ``score``. Nothing in this project confirms a match automatically, and a
model's opinion is not an exception to that — these are decisions about whether
a named person is in a terrorist registry.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from court_monitor.llm.client import LlmUnavailable
from court_monitor.observability import get_logger
from court_monitor.storage.orm import MatchCandidate

_log = get_logger(__name__)

_Schema = TypeVar("_Schema", bound=BaseModel)

# How much of the document to send. Telegram posts average 765 characters, so
# this covers essentially all of them while bounding a pathological outlier.
_MAX_DOCUMENT_CHARS = 4000


class Verdict(StrEnum):
    """What the model concluded. ``insufficient`` is a first-class answer.

    A model that must choose between "yes" and "no" on a passing mention will
    invent a reason for one of them; letting it say the text does not settle the
    question is what keeps the other two verdicts worth reading.
    """

    consistent = "consistent"
    contradicts = "contradicts"
    insufficient = "insufficient"


class Disambiguation(BaseModel):
    """One judgement about one candidate."""

    model_config = ConfigDict(extra="ignore")

    verdict: Verdict
    quote: str = Field(
        min_length=1,
        description="дословная цитата из текста документа, на которой основан вывод",
    )
    reasoning: str = Field(min_length=1, description="одно предложение с обоснованием")


class SupportsJsonCompletion(Protocol):
    """The slice of the LLM client this module needs (D-005's ``LlmExtractor``)."""

    def complete_json(self, prompt: str, *, schema: type[_Schema]) -> _Schema: ...


_PROMPT = """Ты помогаешь оператору OSINT-мониторинга уголовных дел. Твоя задача — \
дать суждение, а не принять решение: окончательный вывод делает человек.

Текст документа:
\"\"\"{document}\"\"\"

Имя, найденное в тексте: {name}
Цитата, из которой оно извлечено: {fact_quote}

Запись реестра Росфинмониторинга:
  ФИО: {record_name}
  дата рождения: {birth_date}
  место рождения: {birth_place}
Однофамильцев с этой фамилией в реестре: {namesakes}

Вопрос: лицо, описанное в документе, — это тот же человек, что и в записи реестра?

Опирайся только на текст документа. Если в нём нет данных, позволяющих отличить \
этого человека от однофамильца, отвечай "insufficient" — это нормальный ответ, \
не пытайся угадать. Обрати внимание на отчество, год рождения, должность и \
любые другие признаки, если они есть в тексте.

В поле quote приведи дословную цитату из текста документа, на которой основан вывод."""


class LlmDisambiguator:
    """Records a model's judgement on a candidate. Never changes its status."""

    def __init__(self, client: SupportsJsonCompletion) -> None:
        self._client = client

    def judge(self, candidate: MatchCandidate) -> Disambiguation | None:
        """Judge one candidate. Returns the model's answer, or ``None`` on failure.

        An unavailable model degrades to ``None`` rather than raising — the same
        contract the NER extractor follows, and for the same reason: an optional
        component being down must not cost the operator the work the pipeline
        already did.

        Persistence (writing ``llm_verdict``, ``llm_quote``, ``llm_reasoning``
        and flushing) is the caller's responsibility — this module only talks to
        the model, it does not open a session.
        """
        fact = candidate.extracted_fact
        record = candidate.person_record
        if fact is None or record is None or fact.document is None:
            _log.warning("llm.disambiguate.incomplete", candidate_id=candidate.id)
            return None

        try:
            result = self._client.complete_json(_build_prompt(candidate), schema=Disambiguation)
        except LlmUnavailable as exc:
            _log.error("llm.disambiguate.unavailable", candidate_id=candidate.id, error=str(exc))
            return None

        _log.info("llm.disambiguate.done", candidate_id=candidate.id, verdict=str(result.verdict))
        return result


def _build_prompt(candidate: MatchCandidate) -> str:
    fact = candidate.extracted_fact
    record = candidate.person_record
    return _PROMPT.format(
        document=(fact.document.text or "")[:_MAX_DOCUMENT_CHARS],
        name=fact.value,
        fact_quote=fact.quote or "—",
        record_name=record.raw_name,
        birth_date=record.birth_date or "не указана",
        birth_place=record.birth_place or "не указано",
        namesakes="неизвестно" if candidate.namesakes is None else candidate.namesakes,
    )
