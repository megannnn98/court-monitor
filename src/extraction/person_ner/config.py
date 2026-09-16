"""How the person recognizer is configured, in one place rather than as magic strings."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

# Chosen on the comparison in docs/wiki/Person-NER.md: standard B-/I-PER labels, Russian
# among its training languages, and it clears the hard negatives the rule extractor fails.
DEFAULT_MODEL = "Babelscape/wikineural-multilingual-ner"
# Pinned so a silent upstream retrain cannot change extraction under us.
DEFAULT_REVISION = "main"
DEFAULT_MIN_SCORE = 0.5


class PersonExtractionStrategy(StrEnum):
    """Who owns person-name detection.

    There is always exactly one owner, so the rule extractor and the model can never
    produce two mentions of the same name.
    """

    RULE_BASED = "rule_based"
    NER = "ner"


@dataclass(frozen=True)
class PersonNerSettings:
    strategy: PersonExtractionStrategy
    model_id: str
    revision: str | None
    device: str | None
    min_score: float

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> PersonNerSettings:
        env = os.environ if env is None else env
        raw_strategy = (env.get("PERSON_EXTRACTION_STRATEGY") or "").strip().lower()
        if raw_strategy and raw_strategy not in set(PersonExtractionStrategy):
            raise ValueError(
                "PERSON_EXTRACTION_STRATEGY must be one of "
                f"{', '.join(sorted(PersonExtractionStrategy))}; got {raw_strategy!r}"
            )
        strategy = (
            PersonExtractionStrategy(raw_strategy)
            if raw_strategy
            else PersonExtractionStrategy.RULE_BASED
        )

        raw_score = (env.get("PERSON_NER_MIN_SCORE") or "").strip()
        try:
            min_score = float(raw_score) if raw_score else DEFAULT_MIN_SCORE
        except ValueError:
            raise ValueError(f"PERSON_NER_MIN_SCORE must be a number; got {raw_score!r}") from None
        if not 0.0 <= min_score <= 1.0:
            raise ValueError(f"PERSON_NER_MIN_SCORE must be between 0 and 1; got {min_score}")

        device = (env.get("PERSON_NER_DEVICE") or "").strip() or None
        return cls(
            strategy=strategy,
            model_id=(env.get("PERSON_NER_MODEL") or "").strip() or DEFAULT_MODEL,
            revision=(env.get("PERSON_NER_REVISION") or "").strip() or DEFAULT_REVISION,
            device=device,
            min_score=min_score,
        )
