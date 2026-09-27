"""The embedding screen before the junk purge: its model file, its scores, and that a
screen turned on and broken fails before anything is deleted."""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from pathlib import Path

import pytest

from monitoring import junk_screen
from monitoring.junk_screen import (
    DEFAULT_MODEL_PATH,
    EmbeddingScreen,
    JunkScreenError,
    ScreenModel,
    screen_from_env,
)


class FakeEmbedder:
    model_id = "fake"
    dimension = 2

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.texts: list[str] = []

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if self.fail:
            raise RuntimeError("CUDA out of memory")
        self.texts += texts
        return [[1.0, 0.0] if "арест" in text else [0.0, 1.0] for text in texts]


def _model(tmp_path: Path, **changes: object) -> Path:
    data = {
        "version": "junk-screen-test",
        "model_id": "fake",
        "article_chars": 20,
        "coefficients": [4.0, -4.0],
        "intercept": 0.0,
        "cutoff": 0.5,
        **changes,
    }
    path = tmp_path / "model.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_scores_are_the_logistic_regression_of_the_article_start(tmp_path: Path) -> None:
    embedder = FakeEmbedder()
    screen = EmbeddingScreen(ScreenModel.load(_model(tmp_path)), embedder)

    scores = screen.scores([("Суд", "арест активиста и ещё длинный текст"), ("Погода", "дождь")])

    assert scores == pytest.approx([1 / (1 + math.exp(-4)), 1 / (1 + math.exp(4))])
    assert screen.cutoff == 0.5 and screen.name == "junk-screen-test:fake"
    # The title, then the start of the text, as the model was fitted on.
    assert embedder.texts[0] == "Суд. арест активиста и ещ"


@pytest.mark.parametrize(
    "changes",
    [{"cutoff": 1.5}, {"coefficients": []}, {"article_chars": 0}, {"intercept": "x"}],
)
def test_a_model_file_that_cannot_judge_is_refused(tmp_path: Path, changes: dict) -> None:
    with pytest.raises(JunkScreenError):
        ScreenModel.load(_model(tmp_path, **changes))


def test_a_missing_model_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(JunkScreenError):
        ScreenModel.load(tmp_path / "missing.json")


def test_an_embedding_failure_or_a_wrong_dimension_judges_nothing(tmp_path: Path) -> None:
    broken = EmbeddingScreen(ScreenModel.load(_model(tmp_path)), FakeEmbedder(fail=True))
    wrong = EmbeddingScreen(
        ScreenModel.load(_model(tmp_path, coefficients=[1.0, 1.0, 1.0])), FakeEmbedder()
    )

    with pytest.raises(JunkScreenError, match="could not embed"):
        broken.scores([("Суд", "арест")])
    with pytest.raises(JunkScreenError, match="dimensions"):
        wrong.scores([("Суд", "арест")])


def test_the_screen_is_off_unless_turned_on() -> None:
    assert screen_from_env({}) is None
    assert screen_from_env({"JUNK_SCREEN": "0"}) is None


def test_turned_on_the_screen_is_loaded_and_tried_before_the_purge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    made: list[FakeEmbedder] = []

    def embedder(config: object) -> FakeEmbedder:
        made.append(FakeEmbedder())
        return made[-1]

    monkeypatch.setattr(junk_screen, "SentenceTransformerEmbedder", embedder)

    screen = screen_from_env({"JUNK_SCREEN": "1", "JUNK_SCREEN_MODEL": str(_model(tmp_path))})

    assert screen is not None and len(made[0].texts) == 1


def test_turned_on_and_broken_the_screen_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        junk_screen, "SentenceTransformerEmbedder", lambda _c: FakeEmbedder(fail=True)
    )

    with pytest.raises(JunkScreenError):
        screen_from_env({"JUNK_SCREEN": "1", "JUNK_SCREEN_MODEL": str(tmp_path / "none.json")})
    with pytest.raises(JunkScreenError):
        screen_from_env({"JUNK_SCREEN": "1", "JUNK_SCREEN_MODEL": str(_model(tmp_path))})


def test_the_shipped_model_is_the_measured_one() -> None:
    model = ScreenModel.load(DEFAULT_MODEL_PATH)

    # multilingual-e5-base, 768 dimensions; the cutoff chosen on the calibration part.
    assert model.model_id == "intfloat/multilingual-e5-base"
    assert len(model.coefficients) == 768
    assert model.cutoff == pytest.approx(0.5202)
