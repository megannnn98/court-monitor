"""«Тест модели»: one real call of the entity model, through the production extractor.

The names step's own `OpenRouterNameNormalizer` reads a short Russian text about a case
whose names are known beforehand. The model passes only when its answer is JSON by the
schema the step expects and every item gets its answer back — HTTP 200 is not enough.
Nothing is written to the entities: the answer is shown and dropped. What the call cost
goes to `entity_model_spend` as `model-test`, so a test is no hidden expense.

One test at a time across every API worker (a PostgreSQL advisory lock): a second press
is refused, never queued, so presses cannot pile up paid calls.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from db.locks import try_advisory_lock
from db.models.settings import EntityModelSpendRecord
from llm.chat import OPENROUTER_URL, Endpoint, ModelHttpError, ModelTimeoutError, Spend
from llm.model_choice import CATALOG, Catalog, mark_tested, model_info
from processing.identity.normalizer import NameItem, NameNormalizerError, OpenRouterNameNormalizer

logger = logging.getLogger("entities")

LOCK_KEY = "entity-model-test"
TIMEOUT_SECONDS = 60.0
COMMAND = "model-test"

TEXT = (
    "Мещанский суд Москвы арестовал Александра Моора по делу о «фейках» об армии. "
    "Его адвокат Ольга Щекина сказала, что обжалует арест. Сторонники пришли к суду "
    "с плакатом «Свободу политзаключённым»."
)
# The items as the names step builds them: the forms the text wrote, and a quote.
ITEMS: tuple[NameItem, ...] = (
    NameItem(1, ("Александра Моора",), TEXT),
    NameItem(2, ("Ольга Щекина",), TEXT),
    NameItem(3, ("Свободу политзаключённым",), TEXT),
)
# (nominative, is_person) expected of each item.
EXPECTED: dict[int, tuple[str, bool]] = {
    1: ("Александр Моор", True),
    2: ("Ольга Щекина", True),
    3: ("", False),
}


@dataclass(frozen=True)
class FoundEntity:
    source: str
    nominative: str
    gender: str
    is_person: bool
    as_expected: bool


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    model: str
    seconds: float
    prompt_tokens: int = 0
    completion_tokens: int = 0
    # What OpenRouter reported; None when it reported none (then `estimated_cost_usd`).
    cost_usd: float | None = None
    estimated_cost_usd: float | None = None
    entities: list[FoundEntity] = field(default_factory=list)
    expected_matched: int = 0
    error: str | None = None


class ProbeBusy(Exception):
    """Another test of a model is running."""


def _reason(exc: BaseException) -> str:
    cause = exc.__cause__ or exc
    if isinstance(cause, ModelTimeoutError):
        return f"Модель не ответила за {TIMEOUT_SECONDS:.0f} с (timeout)."
    if isinstance(cause, ModelHttpError) and cause.status_code == 429:
        return (
            "OpenRouter ответил 429: лимит запросов (у бесплатных моделей он жёсткий). "
            "Повторите позже; на другую модель тест не переключается."
        )
    if isinstance(cause, ModelHttpError):
        return f"Ошибка провайдера: {cause}"
    text = str(exc)
    if text.startswith("unusable answer"):
        return f"Ответ не по схеме извлечения ({text}): модель не годится для шага."
    return f"Ошибка: {text}"


def _estimate(model: str, spend: Spend, catalog: Catalog) -> float | None:
    price = model_info(model, catalog).price
    if price is None:
        return None
    return round(
        (spend.prompt_tokens * price.prompt + spend.completion_tokens * price.completion)
        / 1_000_000,
        6,
    )


def probe(
    model: str,
    api_key: str,
    *,
    http_client: httpx.Client | None = None,
    catalog: Catalog | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> ProbeResult:
    """One call of `model` on `ITEMS`; never raises for what the model did."""
    spend = Spend()
    normalizer = OpenRouterNameNormalizer(
        api_key,
        model=model,
        http_client=http_client or httpx.Client(),
        endpoint=Endpoint("openrouter", OPENROUTER_URL, model, api_key),
        spend=spend,
        timeout_seconds=TIMEOUT_SECONDS,
    )
    started = clock()
    error: str | None = None
    answers = {}
    try:
        answers = normalizer.normalize(ITEMS)
    except NameNormalizerError as exc:
        error = _reason(exc)
    seconds = round(clock() - started, 2)
    entities = []
    for item in ITEMS:
        name = answers.get(item.id)
        if name is None:
            continue
        nominative, person = EXPECTED[item.id]
        entities.append(
            FoundEntity(
                source=name.source,
                nominative=name.nominative,
                gender=name.gender,
                is_person=name.is_person,
                as_expected=name.is_person == person
                and (not person or name.nominative.strip() == nominative),
            )
        )
    if error is None and len(answers) != len(ITEMS):
        error = (
            f"Модель ответила на {len(answers)} из {len(ITEMS)} записей: шаг сочтёт "
            "остальные неотвеченными."
        )
    reported = spend.calls > 0 and spend.uncosted_calls == 0
    return ProbeResult(
        ok=error is None,
        model=model,
        seconds=seconds,
        prompt_tokens=spend.prompt_tokens,
        completion_tokens=spend.completion_tokens,
        cost_usd=round(spend.cost_usd, 6) if reported else None,
        estimated_cost_usd=(
            None
            if reported or not spend.calls
            else _estimate(model, spend, catalog or CATALOG.get())
        ),
        entities=entities,
        expected_matched=sum(entity.as_expected for entity in entities),
        error=error,
    )


def run_probe(
    engine: Engine,
    session_factory: sessionmaker[Session],
    model: str,
    api_key: str,
    *,
    run: Callable[[str, str], ProbeResult] | None = None,
) -> ProbeResult:
    """`probe` under the one-test lock; its cost booked, a pass remembered for the choice.
    ProbeBusy when another test is running."""
    with try_advisory_lock(engine, LOCK_KEY) as acquired:
        if not acquired:
            raise ProbeBusy
        result = (run or probe)(model, api_key)
    logger.info(
        "event=entity_model_tested model=%s ok=%s seconds=%.2f prompt_tokens=%d "
        "completion_tokens=%d cost_usd=%s error=%s",
        model,
        result.ok,
        result.seconds,
        result.prompt_tokens,
        result.completion_tokens,
        result.cost_usd,
        result.error,
    )
    with session_factory.begin() as session:
        if result.prompt_tokens or result.completion_tokens or result.cost_usd:
            uncosted = result.cost_usd is None
            session.add(
                EntityModelSpendRecord(
                    command=COMMAND,
                    model=model,
                    calls=1,
                    prompt_tokens=result.prompt_tokens,
                    completion_tokens=result.completion_tokens,
                    cost_usd=result.cost_usd or 0.0,
                    uncosted_calls=int(uncosted),
                    uncosted_prompt_tokens=result.prompt_tokens if uncosted else 0,
                    uncosted_completion_tokens=result.completion_tokens if uncosted else 0,
                )
            )
        if result.ok:
            mark_tested(session, model)
    return result
