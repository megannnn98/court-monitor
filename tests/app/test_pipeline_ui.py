"""The manual pipeline warns before UI stages that can spend money on DeepSeek."""

from __future__ import annotations

import pytest

from web.ui.entities import _collect_bar
from web.ui.pipeline import PipelineState, deepseek_confirmation, stepper


def test_deepseek_confirmation_is_empty_without_paid_deepseek(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    assert deepseek_confirmation("entities") == ""
    assert "DeepSeek" not in stepper(PipelineState("entities"), 0)


def test_pipeline_warns_before_each_paid_deepseek_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("ENTITY_NORMALIZE_MODEL", raising=False)

    for stage in ("entities", "figurants", "political"):
        page = stepper(PipelineState(stage), 0)
        assert "DeepSeek" in page
        assert "return confirm" in page


def test_entities_page_warns_before_paid_deepseek(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    page = _collect_bar(PipelineState("entities"), None)

    assert "DeepSeek" in page
    assert "return confirm" in page


def test_custom_non_deepseek_openrouter_model_has_no_deepseek_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("ENTITY_NORMALIZE_MODEL", "qwen/qwen3-8b")

    assert deepseek_confirmation("political") == ""


def test_free_deepseek_model_has_no_paid_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("ENTITY_NORMALIZE_MODEL", "deepseek/deepseek-v4.1-flash:free")

    assert deepseek_confirmation("entities") == ""
