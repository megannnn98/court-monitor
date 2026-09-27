"""The embedding screen before the junk purge: articles the rule-based extraction found no
criminal-case event in, held back when they read like one.

The rules miss an event now and then — a digest, a wording they do not know — and the
purge then wipes the article's text for good. The screen embeds the article's start and
scores it with a logistic regression fitted on labelled articles
(`evaluation/junk_screen`, `junk_screen_model.json`). At or above the model's cutoff the
article is held (`JunkScreenHoldRecord`) for a person, never kept silently: a high score
is no proof of a criminal case, and a held article has no event, so no later step sees it
until its extraction is fixed and run again.

Off by default. `JUNK_SCREEN=1` turns it on for `purge-junk`; `JUNK_SCREEN_MODEL` points
to another model file. Turned on, a model file that is missing or wrong, an embedding
model that does not load, or an embedding that fails stops the purge before it deletes
the batch — an article the screen could not judge is never deleted.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from monitoring.embedder import (
    EmbeddingConfig,
    EmbeddingConfigurationError,
    SentenceTransformerEmbedder,
    TextEmbedder,
)

DEFAULT_MODEL_PATH = Path(__file__).with_name("junk_screen_model.json")
HELD = "held"
JUNK = "junk"


class JunkScreenError(Exception):
    """The screen is on and cannot judge: nothing must be deleted."""


class ArticleScreen(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def cutoff(self) -> float: ...

    def scores(self, articles: Sequence[tuple[str, str]]) -> list[float]:
        """A score per (title, text): the higher, the likelier a criminal case."""
        ...


@dataclass(frozen=True)
class ScreenModel:
    version: str
    model_id: str
    article_chars: int
    coefficients: tuple[float, ...]
    intercept: float
    cutoff: float

    @classmethod
    def load(cls, path: Path) -> ScreenModel:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            model = cls(
                version=str(data["version"]),
                model_id=str(data["model_id"]),
                article_chars=int(data["article_chars"]),
                coefficients=tuple(float(value) for value in data["coefficients"]),
                intercept=float(data["intercept"]),
                cutoff=float(data["cutoff"]),
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise JunkScreenError(f"Cannot read the junk screen model {path}: {exc}") from exc
        if not model.coefficients or not 0 < model.cutoff < 1 or model.article_chars < 1:
            raise JunkScreenError(f"The junk screen model {path} is not usable")
        return model


class EmbeddingScreen:
    """The screen the model file describes, over an embedder of that model."""

    def __init__(self, model: ScreenModel, embedder: TextEmbedder) -> None:
        self._model = model
        self._embedder = embedder

    @property
    def name(self) -> str:
        return f"{self._model.version}:{self._model.model_id}"

    @property
    def cutoff(self) -> float:
        return self._model.cutoff

    def scores(self, articles: Sequence[tuple[str, str]]) -> list[float]:
        if not articles:
            return []
        limit = self._model.article_chars
        texts = [f"{title}. {body[:limit]}" for title, body in articles]
        try:
            vectors = self._embedder.embed_documents(texts)
        except Exception as exc:  # any failure of the model: judge nothing, delete nothing
            raise JunkScreenError(f"The junk screen could not embed: {exc}") from exc
        if len(vectors) != len(texts):
            raise JunkScreenError("The junk screen got fewer embeddings than articles")
        return [self._score(vector) for vector in vectors]

    def _score(self, vector: Sequence[float]) -> float:
        if len(vector) != len(self._model.coefficients):
            raise JunkScreenError(
                f"The embedding has {len(vector)} dimensions, the screen expects "
                f"{len(self._model.coefficients)}"
            )
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        logit = self._model.intercept + sum(
            weight * value / norm
            for weight, value in zip(self._model.coefficients, vector, strict=True)
        )
        return 1 / (1 + math.exp(-logit))


def screen_from_env(env: Mapping[str, str] | None = None) -> EmbeddingScreen | None:
    """The screen when `JUNK_SCREEN` is on; None when off. On, it is loaded and tried once
    here, so a broken screen fails before the purge deletes anything."""
    env = os.environ if env is None else env
    if env.get("JUNK_SCREEN", "").strip().lower() not in ("1", "true", "yes", "on"):
        return None
    model = ScreenModel.load(Path(env.get("JUNK_SCREEN_MODEL") or DEFAULT_MODEL_PATH))
    try:
        config = EmbeddingConfig.from_env(model.model_id, env)
    except EmbeddingConfigurationError as exc:
        raise JunkScreenError(f"The junk screen's embedding settings: {exc}") from exc
    screen = EmbeddingScreen(model, SentenceTransformerEmbedder(config))
    screen.scores([("Проверка", "Проверка модели отсева перед очисткой.")])
    return screen


def reason(score: float, cutoff: float) -> str:
    return (
        f"Извлечение не нашло уголовного события, но модель отсева оценила статью в "
        f"{score:.2f} (порог {cutoff:.2f}): возможно, событие пропущено. Оценка — не "
        "доказательство уголовного дела."
    )
