"""Which model the entity steps ask, chosen in the console, and what it costs.

The entity steps (names, figurants, politics, news, unnamed figurants, sentences, digests)
ask one model through `llm.chat`; the junk screen's Jev is not one of them and keeps its
own settings. The model was `ENTITY_NORMALIZE_MODEL` alone; now the operator picks one of
`ENTITY_MODELS` on «Модель извлечения», and the choice is kept in `app_settings`:

- no choice stored: the environment's model, else DeepSeek (`llm.chat.OPENROUTER_MODEL`)
  — an old installation keeps what it had;
- a run is started with the model its warning showed (`--entity-model`), so a choice
  saved while it runs does not change it; a step command started without one (by hand,
  by a schedule) reads the choice once at its start and logs it with its price;
- nothing falls back to another model: a model that fails is a failure.

Prices come from OpenRouter's public catalog (`/api/v1/models`, no key), cached for an
hour. When it cannot be asked, the last prices known are shown as such, with their time;
with none known, the price is «неизвестна», never zero.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from db.models.settings import AppSettingRecord, EntityModelSpendRecord
from llm.chat import OPENROUTER_MODEL, ModelTally, Purpose, is_free_model, model_from_env

logger = logging.getLogger("entities")

MODELS_URL = "https://openrouter.ai/api/v1/models"
CATALOG_SECONDS = 3600.0
# A failed catalog is asked again after this, not on every page.
RETRY_SECONDS = 60.0
# Pages draw their warning from the catalog: as short as the balance's (`web.ui.spend`).
TIMEOUT_SECONDS = 4.0
# The stored choice of each kind: the extraction keeps its old key, so an installation
# that chose before the split keeps its choice for extraction.
SETTING_KEYS: dict[Purpose, str] = {"extraction": "entity_model", "decision": "decision_model"}
SETTING_KEY = SETTING_KEYS["extraction"]
# `<prefix><model>`: when the model last passed this kind's «Тест» (an ISO time).
TESTED_PREFIXES: dict[Purpose, str] = {
    "extraction": "entity_model_tested:",
    "decision": "decision_model_tested:",
}
TESTED_PREFIX = TESTED_PREFIXES["extraction"]
DEFAULT_MODEL = OPENROUTER_MODEL


@dataclass(frozen=True)
class ModelOption:
    id: str
    label: str


# The models the console offers. Ids checked against OpenRouter on 16.10.2026; whether
# each is listed now, and at what price, the catalog tells at runtime.
ENTITY_MODELS: tuple[ModelOption, ...] = (
    ModelOption(DEFAULT_MODEL, "DeepSeek V4.1 Flash (текущая)"),
    ModelOption("deepseek/deepseek-v4-pro", "DeepSeek V4 Pro"),
    ModelOption("xiaomi/mimo-v2.6-flash", "Xiaomi MiMo V2.6 Flash"),
    ModelOption("xiaomi/mimo-v2.6-pro", "Xiaomi MiMo V2.6 Pro"),
    ModelOption("qwen/qwen3.8-flash", "Qwen 3.8 Flash"),
    ModelOption("qwen/qwen3.7-plus", "Qwen 3.7 Plus"),
    ModelOption("stealth/space-bunny-alpha", "Space Bunny Alpha"),
    ModelOption("nvidia/nemotron-3.5-super-vl:free", "NVIDIA Nemotron 3.5 Super VL (free)"),
    ModelOption("nvidia/nemotron-3.5-super-vl", "NVIDIA Nemotron 3.5 Super VL"),
)
MODEL_IDS = frozenset(option.id for option in ENTITY_MODELS)
# Models taken off the list, and why: one line each, so none comes back blind.
EXCLUDED_MODELS: dict[str, str] = {
    # 16.10.2026: OpenRouter answers HTTP 400 «Reasoning is mandatory for this endpoint
    # and cannot be disabled»; `llm.chat.chat_json` disables reasoning for every step.
    "openai/gpt-oss-120b": "требует reasoning, а шаги его отключают",
}


@dataclass(frozen=True)
class Price:
    """USD per million tokens."""

    prompt: float
    completion: float

    @property
    def free(self) -> bool:
        return self.prompt == 0 and self.completion == 0


@dataclass(frozen=True)
class CatalogEntry:
    id: str
    name: str
    price: Price | None
    structured_outputs: bool


@dataclass(frozen=True)
class Catalog:
    """The catalog as last read. `fetched_at` None: never read; `stale`: the last read
    failed and these entries are older."""

    entries: Mapping[str, CatalogEntry]
    fetched_at: datetime | None
    stale: bool = False
    error: str | None = None

    @property
    def known(self) -> bool:
        return self.fetched_at is not None


def _per_million(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    # OpenRouter's routers carry -1: a price that depends on the route.
    return None if number < 0 else round(number * 1_000_000, 6)


def parse_catalog(payload: object) -> dict[str, CatalogEntry]:
    """Every model of the answer that has an id; a broken answer raises ValueError."""
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("no data list")  # noqa: TRY004 - one error for any broken answer
    entries: dict[str, CatalogEntry] = {}
    for item in payload["data"]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        pricing = item.get("pricing")
        if not isinstance(pricing, dict):
            pricing = {}
        prompt = _per_million(pricing.get("prompt"))
        completion = _per_million(pricing.get("completion"))
        parameters = item.get("supported_parameters") or []
        entries[item["id"]] = CatalogEntry(
            id=item["id"],
            name=str(item.get("name") or item["id"]),
            price=Price(prompt, completion)
            if prompt is not None and completion is not None
            else None,
            structured_outputs="structured_outputs" in parameters,
        )
    return entries


def _request_catalog() -> object:
    response = httpx.get(MODELS_URL, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


class CatalogCache:
    """The catalog for every page of a process: read at most once an hour, and after a
    failure not again for a minute; a failure keeps the entries read before."""

    def __init__(
        self,
        request: Callable[[], object] = _request_catalog,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._request = request
        self._now = now
        self._lock = threading.Lock()
        self._catalog = Catalog({}, None)
        self._asked_at: float | None = None
        self._ok = False

    def get(self) -> Catalog:
        with self._lock:
            if self._asked_at is not None:
                age = self._now() - self._asked_at
                if age < (CATALOG_SECONDS if self._ok else RETRY_SECONDS):
                    return self._catalog
            try:
                entries = parse_catalog(self._request())
            except (httpx.HTTPError, ValueError) as exc:
                error = f"{type(exc).__name__}: {exc}"[:200]
                logger.warning("event=openrouter_catalog_failed error=%s", error)
                self._catalog = Catalog(
                    self._catalog.entries, self._catalog.fetched_at, stale=True, error=error
                )
                self._ok = False
            else:
                self._catalog = Catalog(entries, datetime.now(UTC))
                self._ok = True
            self._asked_at = self._now()
            return self._catalog

    def reset_age(self) -> None:
        """The next `get` asks OpenRouter again; the entries read so far stay till then."""
        with self._lock:
            self._asked_at = None

    def reset(self) -> None:
        with self._lock:
            self._catalog = Catalog({}, None)
            self._asked_at = None
            self._ok = False


CATALOG = CatalogCache()


@dataclass(frozen=True)
class ModelInfo:
    """A model as the console shows it."""

    id: str
    label: str
    # In OpenRouter's catalog now; None when the catalog is not known.
    listed: bool | None
    price: Price | None
    price_stale: bool
    structured_outputs: bool | None
    free: bool

    @property
    def usable(self) -> bool:
        """Not known to be unusable: listed and honouring a JSON schema, or unknown."""
        return self.listed is not False and self.structured_outputs is not False


def model_info(model: str, catalog: Catalog) -> ModelInfo:
    label = next((option.label for option in ENTITY_MODELS if option.id == model), model)
    entry = catalog.entries.get(model)
    known = catalog.known
    price = entry.price if entry else None
    return ModelInfo(
        id=model,
        label=label,
        listed=(entry is not None) if known else None,
        price=price,
        price_stale=catalog.stale and price is not None,
        structured_outputs=(entry.structured_outputs if entry else False) if known else None,
        free=is_free_model(model) or (price is not None and price.free),
    )


def price_text(info: ModelInfo, catalog: Catalog) -> str:
    """«$0.04 / $0.70 за 1 млн токенов (вход / выход)», FREE, or why the price is not known."""
    if info.price is None:
        why = (
            "модели нет в каталоге OpenRouter"
            if info.listed is False
            else "каталог OpenRouter не ответил"
            if not catalog.known
            else "каталог не назвал цену"
        )
        if info.free:
            return f"FREE: $0 (бесплатный вариант OpenRouter; {why})."
        return f"Цена сейчас неизвестна: {why}."
    if info.free:
        text = "FREE: вход $0, выход $0 за 1 млн токенов."
    else:
        text = (
            f"Вход ${info.price.prompt:.2f} / 1 млн токенов, "
            f"выход ${info.price.completion:.2f} / 1 млн токенов. Платная."
        )
    if info.price_stale and catalog.fetched_at is not None:
        text += f" Цена устарела: последний ответ каталога {catalog.fetched_at:%d.%m.%Y %H:%M} UTC."
    return text


# -- the choice -------------------------------------------------------------------------


def environment_model(env: Mapping[str, str] | None = None, kind: Purpose = "extraction") -> str:
    """The model without a stored choice: what the environment names, else DeepSeek."""
    return model_from_env(os.environ if env is None else env, kind)


def stored_model(session: Session, kind: Purpose = "extraction") -> str | None:
    record = session.get(AppSettingRecord, SETTING_KEYS[kind])
    return record.value if record is not None else None


def chosen_model(
    session: Session, env: Mapping[str, str] | None = None, kind: Purpose = "extraction"
) -> str:
    return stored_model(session, kind) or environment_model(env, kind)


class ChoiceRefused(ValueError):
    pass


def tested_at(session: Session, model: str, kind: Purpose = "extraction") -> str | None:
    record = session.get(AppSettingRecord, TESTED_PREFIXES[kind] + model)
    return record.value if record is not None else None


def mark_tested(session: Session, model: str, kind: Purpose = "extraction") -> None:
    _put(session, TESTED_PREFIXES[kind] + model, datetime.now(UTC).isoformat())


def save_choice(
    session: Session, model: str, catalog: Catalog, kind: Purpose = "extraction"
) -> None:
    """Keep `model` for `kind`. Refused for a model not offered, one the catalog says is
    not listed or does not honour a JSON schema, and one that has not passed this kind's
    «Тест» — except the default, the way back."""
    if model not in MODEL_IDS:
        raise ChoiceRefused(f"Модели {model} нет в списке.")
    info = model_info(model, catalog)
    if info.listed is False:
        raise ChoiceRefused(f"Модели {model} сейчас нет в каталоге OpenRouter.")
    if info.structured_outputs is False:
        raise ChoiceRefused(f"Модель {model} не поддерживает structured outputs (JSON Schema).")
    if model != DEFAULT_MODEL and tested_at(session, model, kind) is None:
        raise ChoiceRefused(f"Сначала проверьте модель {model} кнопкой «Тест».")
    _put(session, SETTING_KEYS[kind], model)
    logger.info("event=entity_model_chosen kind=%s model=%s", kind, model)


def _put(session: Session, key: str, value: str) -> None:
    record = session.get(AppSettingRecord, key)
    if record is None:
        session.add(AppSettingRecord(key=key, value=value))
    else:
        record.value = value


def model_for_step(
    session_factory: sessionmaker[Session],
    env: Mapping[str, str] | None = None,
    kind: Purpose = "extraction",
) -> str:
    """The model a step command started without one asks: the stored choice, read once."""
    with session_factory() as session:
        return chosen_model(session, env, kind)


def log_selected(
    model: str, *, source: str, kind: Purpose = "extraction", catalog: Catalog | None = None
) -> None:
    """The model of a run and its price, for a run nobody confirmed (a schedule, a shell)."""
    info = model_info(model, catalog if catalog is not None else CATALOG.get())
    price = info.price
    logger.info(
        "event=entity_model_selected kind=%s model=%s source=%s free=%s "
        "prompt_usd_per_million=%s completion_usd_per_million=%s price_stale=%s",
        kind,
        model,
        source,
        info.free,
        f"{price.prompt:.4f}" if price else "unknown",
        f"{price.completion:.4f}" if price else "unknown",
        info.price_stale,
    )


# -- what the entity model spent ----------------------------------------------------------


def record_probe_spend(
    session: Session,
    *,
    command: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cost_usd: float | None,
) -> None:
    """Book one model-test call in the transaction that records its test outcome."""
    if not (prompt_tokens or completion_tokens or cost_usd):
        return
    uncosted = cost_usd is None
    session.add(
        EntityModelSpendRecord(
            command=command,
            model=model,
            calls=1,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost_usd or 0.0,
            uncosted_calls=int(uncosted),
            uncosted_prompt_tokens=prompt_tokens if uncosted else 0,
            uncosted_completion_tokens=completion_tokens if uncosted else 0,
        )
    )


def book_ledger(
    session_factory: sessionmaker[Session], command: str, tallies: Mapping[str, ModelTally]
) -> None:
    """One `entity_model_spend` row per model the step asked."""
    rows = [
        EntityModelSpendRecord(
            command=command,
            model=model,
            calls=tally.calls,
            prompt_tokens=tally.prompt_tokens,
            completion_tokens=tally.completion_tokens,
            cost_usd=round(tally.cost_usd, 6),
            uncosted_calls=tally.uncosted_calls,
            uncosted_prompt_tokens=tally.uncosted_prompt_tokens,
            uncosted_completion_tokens=tally.uncosted_completion_tokens,
        )
        for model, tally in tallies.items()
        if tally.calls
    ]
    if not rows:
        return
    with session_factory.begin() as session:
        session.add_all(rows)


@dataclass(frozen=True)
class SpendLine:
    model: str
    calls: int
    prompt_tokens: int
    completion_tokens: int
    # Reported by OpenRouter.
    cost_usd: float
    # Of the calls without a reported cost, by today's price list; None when unknown.
    estimated_usd: float | None
    uncosted_calls: int


@dataclass(frozen=True)
class SpendSummary:
    today: list[SpendLine]
    total: list[SpendLine]


def _lines(rows: list[EntityModelSpendRecord], catalog: Catalog) -> list[SpendLine]:
    by_model: dict[str, list[EntityModelSpendRecord]] = {}
    for row in rows:
        by_model.setdefault(row.model, []).append(row)
    lines = []
    for model, items in sorted(by_model.items()):
        uncosted = sum(row.uncosted_calls for row in items)
        estimate: float | None = 0.0
        if uncosted:
            price = model_info(model, catalog).price
            estimate = (
                round(
                    sum(
                        row.uncosted_prompt_tokens * price.prompt
                        + row.uncosted_completion_tokens * price.completion
                        for row in items
                    )
                    / 1_000_000,
                    6,
                )
                if price is not None
                else None
            )
        lines.append(
            SpendLine(
                model=model,
                calls=sum(row.calls for row in items),
                prompt_tokens=sum(row.prompt_tokens for row in items),
                completion_tokens=sum(row.completion_tokens for row in items),
                cost_usd=round(sum(row.cost_usd for row in items), 6),
                estimated_usd=estimate,
                uncosted_calls=uncosted,
            )
        )
    return lines


def spend_summary(
    session: Session, catalog: Catalog, *, now: datetime | None = None
) -> SpendSummary:
    """Today's (UTC) and all-time spend of the entity model, per model; asks no model."""
    rows = list(session.scalars(select(EntityModelSpendRecord)).all())
    start = (now or datetime.now(UTC)).replace(hour=0, minute=0, second=0, microsecond=0)
    today = [row for row in rows if row.created_at >= start]
    return SpendSummary(today=_lines(today, catalog), total=_lines(rows, catalog))
