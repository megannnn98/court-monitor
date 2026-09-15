"""Versioned evaluation policy: hard gates and quality targets in one file.

Thresholds are data (`evaluation/real_world/policy_v1.json`), hashed into the
report. They are never adjusted by the evaluator and must not be tuned on the
locked test split.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from evaluation.real_world.models import DEFAULT_DATA_DIR, sha256_text

DEFAULT_POLICY_PATH = DEFAULT_DATA_DIR / "policy_v1.json"

# Quality targets where a smaller value is better (everything else: at least).
AT_MOST_TARGETS = frozenset({"max_manual_reviews_per_100_articles"})


class EvaluationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: str
    # Below this many VERIFIED golden articles every result is PRELIMINARY.
    min_verified_articles: int = Field(ge=1)
    min_verified_articles_per_split: int = Field(ge=1)
    # Maximum allowed count per hard gate (all zero in v1).
    hard_gates: dict[str, int]
    # Minimum value per quality metric.
    quality_targets: dict[str, float]
    # Reported, never failing.
    advisory_targets: dict[str, float] = Field(default_factory=dict)


def load_policy(path: Path = DEFAULT_POLICY_PATH) -> tuple[EvaluationPolicy, str]:
    """The policy and the sha256 of its file (provenance)."""
    content = path.read_text(encoding="utf-8")
    return EvaluationPolicy.model_validate_json(content), sha256_text(content)
