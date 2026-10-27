"""Task-centric operator dashboard over the existing pipeline and review queues."""

from __future__ import annotations

from web.views.pipeline import (
    STAGES,
    PipelineState,
    chain_confirmation,
)

PROCESSING_LABELS = {
    "load": "Публикации загружены",
    "purge": "Публикации проверены",
    "entities": "Люди определены",
    "figurants": "Фигуранты определены",
    "political": "Проверка политичности завершена",
}


def chain_question(state: PipelineState, *, with_model: bool = True) -> str:
    """What is asked before «Сделать всё» on «Работа»: the entity model and what it costs.
    Read by the legacy page and `GET /api/v1/cycle` (which sends the model apart)."""
    return chain_confirmation(state.current, with_model=with_model)


def step_statuses(state: PipelineState) -> list[tuple[str, str]]:
    """(stage, status) of each step of the round: done, running, ready or waiting."""
    current = STAGES.index(state.current)
    return [
        (
            stage,
            "done"
            if index < current
            else ("running" if state.live is not None else "ready")
            if index == current
            else "waiting",
        )
        for index, stage in enumerate(STAGES)
    ]
