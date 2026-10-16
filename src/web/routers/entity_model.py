"""«Модель извлечения» for the React console: the models the entity steps may ask, their
prices from OpenRouter's catalog, «Тест», the choice and what the model spent
(`llm.model_choice`). The actions are refused from other origins (`web.csrf`). No answer
carries the OpenRouter key; a test is one call, one at a time."""

import os

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from llm.model_choice import (
    CATALOG,
    DEFAULT_MODEL,
    ENTITY_MODELS,
    ChoiceRefused,
    SpendLine,
    model_info,
    price_text,
    save_choice,
    spend_summary,
    stored_model,
    tested_at,
)
from llm.model_choice import (
    MODEL_IDS as OFFERED,
)
from llm.model_choice import (
    chosen_model as read_chosen_model,
)
from processing.identity.model_probe import DECISION_EXPECTED, EXPECTED, ProbeBusy, run_probe
from web.dependencies import get_db, session_factory_for
from web.entity_model import forget
from web.response_models import (
    EntityModelEntityResponse,
    EntityModelOptionResponse,
    EntityModelRequest,
    EntityModelSettingsResponse,
    EntityModelSpendLineResponse,
    EntityModelTestResponse,
)

router = APIRouter()


def _api_key() -> str:
    return os.environ.get("OPENROUTER_API_KEY", "").strip()


def _line(line: SpendLine) -> EntityModelSpendLineResponse:
    return EntityModelSpendLineResponse(**vars(line))


def _settings(db: Session) -> EntityModelSettingsResponse:
    catalog = CATALOG.get()
    stored = stored_model(db)
    current = read_chosen_model(db)
    decision_stored = stored_model(db, "decision")
    decision = read_chosen_model(db, kind="decision")
    models = []
    ids = [option.id for option in ENTITY_MODELS]
    # A model the environment names that the list does not offer is still shown: it is
    # the one the steps ask until another is chosen.
    ids += [model for model in dict.fromkeys((current, decision)) if model not in OFFERED]
    for model in ids:
        info = model_info(model, catalog)
        models.append(
            EntityModelOptionResponse(
                id=model,
                label=info.label,
                listed=info.listed,
                free=info.free,
                prompt_usd_per_million=info.price.prompt if info.price else None,
                completion_usd_per_million=info.price.completion if info.price else None,
                price_stale=info.price_stale,
                structured_outputs=info.structured_outputs,
                price_text=price_text(info, catalog),
                tested_at=tested_at(db, model),
                decision_tested_at=tested_at(db, model, "decision"),
                usable=info.usable and model in OFFERED,
            )
        )
    spent = spend_summary(db, catalog)
    return EntityModelSettingsResponse(
        current=current,
        source="stored" if stored else "environment",
        decision_current=decision,
        decision_source="stored" if decision_stored else "environment",
        default=DEFAULT_MODEL,
        catalog_fetched_at=catalog.fetched_at,
        catalog_stale=catalog.stale,
        catalog_error=catalog.error,
        models=models,
        spend_today=[_line(line) for line in spent.today],
        spend_total=[_line(line) for line in spent.total],
        openrouter_configured=bool(_api_key()),
    )


@router.get("/entity-model", response_model=EntityModelSettingsResponse)
def get_entity_model(db: Session = Depends(get_db)) -> EntityModelSettingsResponse:  # noqa: B008
    """The models, their prices (the catalog is cached for an hour), the choice and the
    entity model's spend today and in all; asks no model."""
    return _settings(db)


@router.post("/entity-model/catalog/refresh", response_model=EntityModelSettingsResponse)
def refresh_catalog(db: Session = Depends(get_db)) -> EntityModelSettingsResponse:  # noqa: B008
    """Read OpenRouter's prices again now; a failure keeps the old ones, marked stale."""
    CATALOG.reset_age()
    return _settings(db)


@router.post("/entity-model", response_model=EntityModelSettingsResponse)
def choose_entity_model(
    body: EntityModelRequest,
    db: Session = Depends(get_db),  # noqa: B008
) -> EntityModelSettingsResponse:
    """Keep the model for the next runs; a run already started keeps its own. 400 for a
    model not offered, not in the catalog, without structured outputs, or not tested."""
    try:
        save_choice(db, body.model, CATALOG.get(), body.kind)
        db.commit()
    except ChoiceRefused as refused:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(refused)) from None
    forget()
    return _settings(db)


@router.post("/entity-model/test", response_model=EntityModelTestResponse)
def test_entity_model(
    body: EntityModelRequest,
    db: Session = Depends(get_db),  # noqa: B008
) -> EntityModelTestResponse:
    """One real call of the model through the step it would serve — the names step for
    the extraction model, the figurants' for the decision model; nothing is saved to the
    entities. 409 while another test runs; 503 without OpenRouter."""
    if body.model not in OFFERED:
        raise HTTPException(status_code=400, detail=f"Модели {body.model} нет в списке.")
    key = _api_key()
    if not key:
        raise HTTPException(status_code=503, detail="OPENROUTER_API_KEY не задан.")
    factory = session_factory_for(db)
    bind = db.get_bind()
    engine = bind if isinstance(bind, Engine) else bind.engine
    # The request's own transaction ends here: the test books its cost in its own.
    db.rollback()
    try:
        result = run_probe(engine, factory, body.model, key, kind=body.kind)
    except ProbeBusy:
        raise HTTPException(
            status_code=409, detail="Уже идёт тест модели: дождитесь его результата."
        ) from None
    return EntityModelTestResponse(
        ok=result.ok,
        model=result.model,
        seconds=result.seconds,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        cost_usd=result.cost_usd,
        estimated_cost_usd=result.estimated_cost_usd,
        entities=[EntityModelEntityResponse(**vars(entity)) for entity in result.entities],
        expected_matched=result.expected_matched,
        expected_total=len(EXPECTED if body.kind == "extraction" else DECISION_EXPECTED),
        kind=body.kind,
        error=result.error,
    )
