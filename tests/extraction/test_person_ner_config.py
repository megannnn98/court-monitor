import pytest

from extraction.person_ner.config import (
    DEFAULT_MODEL,
    PersonExtractionStrategy,
    PersonNerSettings,
)


def test_defaults_keep_the_rule_extractor_in_charge() -> None:
    """The model only takes over once an evaluation says it should."""
    settings = PersonNerSettings.from_env({})

    assert settings.strategy is PersonExtractionStrategy.RULE_BASED
    assert settings.model_id == DEFAULT_MODEL
    assert settings.min_score == 0.5


def test_every_setting_can_be_overridden() -> None:
    settings = PersonNerSettings.from_env(
        {
            "PERSON_EXTRACTION_STRATEGY": "ner",
            "PERSON_NER_MODEL": "org/model",
            "PERSON_NER_REVISION": "abc123",
            "PERSON_NER_DEVICE": "cpu",
            "PERSON_NER_MIN_SCORE": "0.8",
        }
    )

    assert settings.strategy is PersonExtractionStrategy.NER
    assert (settings.model_id, settings.revision, settings.device) == ("org/model", "abc123", "cpu")
    assert settings.min_score == 0.8


def test_an_unknown_strategy_is_rejected_instead_of_silently_ignored() -> None:
    with pytest.raises(ValueError, match="PERSON_EXTRACTION_STRATEGY"):
        PersonNerSettings.from_env({"PERSON_EXTRACTION_STRATEGY": "magic"})


@pytest.mark.parametrize("value", ["abc", "-0.1", "1.5"])
def test_an_invalid_threshold_is_rejected(value: str) -> None:
    with pytest.raises(ValueError, match="PERSON_NER_MIN_SCORE"):
        PersonNerSettings.from_env({"PERSON_NER_MIN_SCORE": value})
