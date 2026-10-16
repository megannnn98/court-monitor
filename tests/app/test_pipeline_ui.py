"""The manual pipeline warns before UI stages that can spend money on DeepSeek."""

from __future__ import annotations

import pytest

from web.ui.entities import _collect_bar
from web.ui.pipeline import PipelineState, step_confirmation, stepper


def test_deepseek_confirmation_is_empty_without_paid_deepseek(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    assert step_confirmation("entities") == ""
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


def test_another_openrouter_model_is_warned_of_by_its_own_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("ENTITY_NORMALIZE_MODEL", "xiaomi/mimo-v2.6-flash")

    text = step_confirmation("political")

    assert "xiaomi/mimo-v2.6-flash" in text and "Шаг платный" in text
    assert "DeepSeek" not in text


def test_a_free_model_is_warned_of_as_free_with_no_balance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("ENTITY_NORMALIZE_MODEL", "nvidia/nemotron-3.5-super-vl:free")

    text = step_confirmation("entities")

    assert "FREE" in text and "Шаг бесплатный" in text
    assert "Шаг платный" not in text and "Остаток" not in text


def test_a_start_address_carries_only_the_models_its_steps_ask(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A change of the extraction model is no reason to refuse the figurants' step."""
    from web.ui.pipeline import models_query

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    assert models_query(("load",)) == "" and models_query(("purge",)) == ""
    assert models_query(("entities",)).startswith("em=") and "dm=" not in models_query(
        ("entities",)
    )
    assert models_query(("figurants",)).startswith("dm=") and "em=" not in models_query(
        ("figurants",)
    )
    assert "em=" in models_query(("political",)) and "dm=" in models_query(("political",))
    # The chain from step 2: both, for the steps after it.
    assert "em=" in models_query(("purge", "entities", "figurants", "political"))
