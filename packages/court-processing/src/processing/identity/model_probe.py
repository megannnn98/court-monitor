"""«Тест модели»: one real call of the entity model, through the production extractor.

The names step's own `OpenRouterNameNormalizer` reads a short Russian text about a case
whose names are known beforehand. The model passes only when its answer is JSON by the
schema the step expects and every item gets its answer back — HTTP 200 is not enough.
Nothing is written to the entities: the answer is shown and dropped. What the call cost
goes to `entity_model_spend` as `model-test`, so a test is no hidden expense.

The decision model (who is accused, which case is political) is tested apart, through
the figurants' own classifier (`probe_decision`), on a man named beside another's case:
the names test is too easy for it — a free model that passed it accused a commentator.

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
from llm.chat import (
    OPENROUTER_URL,
    Endpoint,
    ModelHttpError,
    ModelTimeoutError,
    Purpose,
    Spend,
)
from llm.model_choice import CATALOG, Catalog, mark_tested, model_info
from processing.identity.normalizer import NameItem, NameNormalizerError, OpenRouterNameNormalizer
from processing.roles.roles import (
    FIGURANT,
    OpenRouterRoleClassifier,
    RoleAnswer,
    RoleClassifierError,
    RoleItem,
    role_of,
)

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
    # The decision test: the role the model gave, and why.
    role: str = ""
    note: str = ""


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
    if error is None:
        problems = []
        if len(answers) != len(ITEMS):
            problems.append(
                f"ответила на {len(answers)} из {len(ITEMS)} записей (шаг сочтёт остальные "
                "неотвеченными)"
            )
        # Valid JSON is not enough: each name as expected, the slogan not a person.
        wrong = ", ".join(f"«{entity.source}»" for entity in entities if not entity.as_expected)
        if wrong:
            problems.append(f"ответила неверно: {wrong}")
        if problems:
            error = f"Модель {'; '.join(problems)}. Для шага имён она не годится."
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


# The decision model's test: the figurants' step on a text where one man is accused and
# another is only named beside his case — the mistake of 16.10.2026, when a free model
# made the founder of a channel, named in a comment, the accused of the case reported.
DECISION_TEXT = (
    "ФСБ задержала в Псковской области гражданина Швеции Эрика Ларссона: против него "
    "возбудили дело о наёмничестве и подготовке теракта, суд арестовал его на два месяца. "
    "Комментируя новость, основатель провластного телеграм-канала Андрей Колесов призвал "
    "создать обменный фонд из иностранцев."
)
# Built on the quote of 16.10.2026, names and plot invented: the man detained abroad and
# another man's Russian case stand in one quote, the detention told by the same verb. A
# free model answered «accused — дела по УК РФ» for the man detained abroad.
DECISION_ABROAD = (
    "ФСБ задержала в Брянской области гражданина Норвегии Ларса Хольма: ему предъявили "
    "обвинение в шпионаже."
)
# The window the step itself sends (`processing.roles.roles.QUOTE_CONTEXT`): cut mid-
# sentence, the speaker a pronoun, the FSB's video credited after the name.
DECISION_ABROAD_QUOTE = (
    "обменный фонд гражданами Норвегии и Швеции, «чтобы выручать своих». Так он отреагировал "
    "на задержание в Сербии автора военного блога «Пеликан» Павла Рудина. Видео: "
    "пресс-служба ФСБ"
)
# A model answering the same question twice may answer it differently (16.10.2026: the
# free model gave foreign, detained, foreign on the very same quote): one pass is no proof.
DECISION_ROUNDS = 3
DECISION_ITEMS: tuple[RoleItem, ...] = (
    RoleItem(1, "Эрик Ларссон", (DECISION_TEXT,)),
    RoleItem(2, "Андрей Колесов", (DECISION_TEXT,)),
    RoleItem(3, "Павел Рудин", (DECISION_ABROAD_QUOTE,)),
    RoleItem(4, "Ларс Хольм", (DECISION_ABROAD,)),
)
# Whether each item must end a figurant (`processing.roles.roles.role_of`). The test cuts
# off the dangerous class — what the step makes of an answer — not the taxonomy: only
# «accused» makes a figurant, and only figurants go on to the politics step. «detained»
# makes a «possible» (a person's review), «support» or «other» a «mentioned»: wrong
# perhaps, but no political prisoner. (16.10.2026: DeepSeek, the model in use, answered
# support and detained for the commentator and the man detained abroad in one round of
# three — and the same model told them apart in the other two.)
DECISION_EXPECTED: dict[int, bool] = {1: True, 2: False, 3: False, 4: True}


def _as_expected(item_id: int, kind: str) -> bool:
    return (role_of(kind) == FIGURANT) == DECISION_EXPECTED[item_id]


def _expected_words(item_id: int) -> str:
    return "обвиняемый (accused)" if DECISION_EXPECTED[item_id] else "не обвиняемый"


def probe_decision(
    model: str,
    api_key: str,
    *,
    http_client: httpx.Client | None = None,
    catalog: Catalog | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> ProbeResult:
    """`DECISION_ROUNDS` calls of `model` through the figurants' classifier; a pass needs,
    in every one, the schema, every item answered and each answer of its expected class.
    The first round that fails ends the test and is the one shown."""
    spend = Spend()
    classifier = OpenRouterRoleClassifier(
        api_key,
        model=model,
        http_client=http_client or httpx.Client(),
        endpoint=Endpoint("openrouter", OPENROUTER_URL, model, api_key),
        spend=spend,
        timeout_seconds=TIMEOUT_SECONDS,
    )
    started = clock()
    error: str | None = None
    answers: dict[int, RoleAnswer] = {}
    for round_number in range(1, DECISION_ROUNDS + 1):
        try:
            answers = classifier.classify(DECISION_ITEMS)
        except RoleClassifierError as exc:
            error = _reason(exc)
            break
        problems = []
        if len(answers) != len(DECISION_ITEMS):
            problems.append(f"ответила на {len(answers)} из {len(DECISION_ITEMS)} записей")
        wrong = [
            f"«{item.name}»: ждали {_expected_words(item.id)}, модель ответила {answer.kind}"
            for item in DECISION_ITEMS
            if (answer := answers.get(item.id)) is not None
            and not _as_expected(item.id, answer.kind)
        ]
        if wrong:
            problems.append("; ".join(wrong))
        if problems:
            error = (
                f"В прогоне {round_number} из {DECISION_ROUNDS} модель {'; '.join(problems)}. "
                "Она приписывает дело не тому человеку или теряет обвиняемого: для шага "
                "фигурантов и политики не годится."
            )
            break
    seconds = round(clock() - started, 2)
    entities = [
        FoundEntity(
            source=answer.source,
            nominative="",
            gender="",
            is_person=True,
            as_expected=_as_expected(item.id, answer.kind),
            role=answer.kind,
            note=answer.explanation,
        )
        for item in DECISION_ITEMS
        if (answer := answers.get(item.id)) is not None
    ]
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
    kind: Purpose = "extraction",
    run: Callable[[str, str], ProbeResult] | None = None,
) -> ProbeResult:
    """The test of `kind` under the one-test lock; its cost booked, a pass remembered for
    that kind's choice. ProbeBusy when another test is running."""
    with try_advisory_lock(engine, LOCK_KEY) as acquired:
        if not acquired:
            raise ProbeBusy
        result = (run or (probe if kind == "extraction" else probe_decision))(model, api_key)
    logger.info(
        "event=entity_model_tested kind=%s model=%s ok=%s seconds=%.2f prompt_tokens=%d "
        "completion_tokens=%d cost_usd=%s error=%s",
        kind,
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
            mark_tested(session, model, kind)
    return result
