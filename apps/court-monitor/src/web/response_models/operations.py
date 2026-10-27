"""Operation runs, pipeline status and model settings."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class OperationRunResponse(BaseModel):
    id: int
    operation: str
    title: str
    parameters: dict[str, object]
    status: str
    created_at: str
    started_at: str | None
    finished_at: str | None
    duration_seconds: float | None
    command: list[str]
    return_code: int | None
    stdout: str
    stderr: str
    error: str | None


class AboutResponse(BaseModel):
    """«О системе»: the build stamp, the totals behind the status strip, and the last
    successful operator run. The same data the legacy `/ui/about` page shows."""

    version: str
    tag: str
    commit: str
    built_at: str
    articles: int
    people: int
    last_successful_run_at: datetime | None
    checked_at: datetime


class QueueResponse(BaseModel):
    """What waits for the operator, by queue; `total` is the menu's «Работа» count."""

    total: int
    pairs: int
    unclear_roles: int
    unclear_verdicts: int
    unnamed: int
    junk_holds: int


class LiveOperationResponse(BaseModel):
    """The step running now: its mode and the title the console gives it."""

    mode: str | None
    title: str


class BalanceResponse(BaseModel):
    """The OpenRouter balance of the strip: the figure («$2.91», a dash when OpenRouter
    did not answer), its hint, and whether it is under one run's limit."""

    figure: str
    hint: str
    low: bool
    known: bool


class LayoutSuspectResponse(BaseModel):
    """A source whose latest runs suggest that its page layout changed."""

    source: str
    signal: str
    run_id: int
    since: datetime
    count: int
    total: int
    text: str


class StatusResponse(BaseModel):
    """The legacy status strip and menu counters: the same numbers on every page."""

    articles: int
    people: int
    result: int
    # People of the result not yet «обработано»: the volume of work left.
    result_open: int
    queue: QueueResponse
    latest_monitoring_status: str | None
    live_operation: LiveOperationResponse | None
    next_action: str
    # None with no OpenRouter key: the console then spends nothing.
    balance: BalanceResponse | None = None
    layout_suspects: list[LayoutSuspectResponse] = []


class CycleTaskResponse(BaseModel):
    """A manual queue of «Работа»: what waits for the operator and where."""

    key: str
    title: str
    count: int
    # The legacy page of the queue.
    href: str
    description: str


class CycleStepResponse(BaseModel):
    stage: str
    number: int
    label: str
    # done, running, ready or waiting.
    status: str


class LiveRunResponse(BaseModel):
    run_id: int
    title: str


class EntityModelNoticeResponse(BaseModel):
    """The entity model before a start: its id, price per million tokens, and kind."""

    model: str
    label: str
    free: bool
    # USD per million tokens; None when the price is not known now.
    prompt_usd_per_million: float | None
    completion_usd_per_million: float | None
    price_stale: bool
    # «платная», «бесплатная» or «цена неизвестна».
    kind: str
    price_text: str


class LastLoadResponse(BaseModel):
    """The latest load that ended well: when, and for what dates (None: no dates, all new)."""

    finished_at: datetime | None
    published_from: date | None
    published_to: date | None


class CycleResponse(BaseModel):
    """«Работа»: the one review to do first, the other queues, and the automatic steps
    with the one press that runs what is left of the round."""

    attention: CycleTaskResponse | None
    tasks: list[CycleTaskResponse]
    steps: list[CycleStepResponse]
    current_stage: str
    live: LiveRunResponse | None
    # The latest run this answer was read for: «Сделать всё» names it, so an old press
    # sent again cannot start a second round.
    latest_run_id: int
    chain_span: str
    # What to ask before «Сделать всё»: an unfinished review, what is deleted, the cost.
    chain_question: str
    chain_note: str
    chain_stopped: str
    # The entity model the round's steps would ask, as the warning shows it; None when no
    # step left asks it, or without OpenRouter.
    entity_model: EntityModelNoticeResponse | None = None
    # The same of the decision model (figurants, politics, news).
    decision_model: EntityModelNoticeResponse | None = None
    # `PIPELINE_SINCE`: step 2 deletes what was published before it; a period starts on it
    # or after it. None when unset.
    working_date: date | None = None
    last_load: LastLoadResponse | None = None
    # What to ask before a press with dates: the round from step 1, with what it costs.
    period_question: str = ""


class CycleStartRequest(BaseModel):
    """«Сделать всё»: the steps from the current one to the end of the round."""

    after: int
    # A period to load: with a date «с», the round is steps 1–5 and its load takes what was
    # published from it to `published_to` (None: to now), going past what is stored.
    published_from: date | None = None
    published_to: date | None = None
    # The entity model the warning showed: the start is refused (409) when the choice
    # changed since, so the round never asks a model nobody saw.
    entity_model: str | None = None
    decision_model: str | None = None


class CycleStartResponse(BaseModel):
    run_id: int
    stage: str


class StopResponse(BaseModel):
    run_id: int
    # False when the run had already ended: nothing was changed.
    stopped: bool


class RunStartedResponse(BaseModel):
    run_id: int


class EntityModelOptionResponse(BaseModel):
    id: str
    label: str
    # In OpenRouter's catalog now; None when the catalog could not be read.
    listed: bool | None
    free: bool
    prompt_usd_per_million: float | None
    completion_usd_per_million: float | None
    price_stale: bool
    structured_outputs: bool | None
    price_text: str
    # When it last passed «Тест»; the choice of any but the default needs one.
    tested_at: str | None
    # When it last passed the decision model's «Тест» (figurants).
    decision_tested_at: str | None = None
    usable: bool


class EntityModelSpendLineResponse(BaseModel):
    model: str
    calls: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    # Of the calls OpenRouter told no cost for, by the price list: an estimate.
    estimated_usd: float | None
    uncosted_calls: int


class EntityModelSettingsResponse(BaseModel):
    """«Модель извлечения»: the models, their prices, the choice and what it spent."""

    current: str
    # «stored» — chosen here; «environment» — no choice yet, the environment's model.
    source: str
    # The decision model: who is accused, which case is political, what the news is.
    decision_current: str
    decision_source: str
    default: str
    catalog_fetched_at: datetime | None
    catalog_stale: bool
    catalog_error: str | None
    models: list[EntityModelOptionResponse]
    spend_today: list[EntityModelSpendLineResponse]
    spend_total: list[EntityModelSpendLineResponse]
    openrouter_configured: bool


class EntityModelRequest(BaseModel):
    model: str = Field(max_length=200)
    # Which of the two choices: «extraction» (names, unnamed, sentences, digests) or
    # «decision» (figurants, politics, news).
    kind: Literal["extraction", "decision"] = "extraction"


class EntityModelEntityResponse(BaseModel):
    source: str
    nominative: str
    gender: str
    is_person: bool
    as_expected: bool
    # The decision test: the role given, and why.
    role: str = ""
    note: str = ""


class EntityModelTestResponse(BaseModel):
    ok: bool
    model: str
    seconds: float
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float | None
    estimated_cost_usd: float | None
    entities: list[EntityModelEntityResponse]
    expected_matched: int
    expected_total: int
    error: str | None
    kind: str = "extraction"
