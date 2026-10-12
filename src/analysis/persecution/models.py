from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field


class PersecutionClassificationStatus(StrEnum):
    POLITICAL = "political"
    NON_POLITICAL = "non_political"
    UNCERTAIN = "uncertain"
    NEEDS_REVIEW = "needs_review"


class PersecutionEvidenceType(StrEnum):
    POLITICAL_ARTICLE = "political_article"
    POLITICAL_EVENT = "political_event"
    POLITICAL_CHARGE = "political_charge"
    HUMAN_RIGHTS_DEFENDER = "human_rights_defender"
    JOURNALIST = "journalist"
    ACTIVIST = "activist"
    DISSENTING_OPINION = "dissenting_opinion"
    RELIGIOUS_PERSECUTION = "religious_persecution"
    ANTI_WAR_ACTIVITY = "anti_war_activity"
    LGBT_PERSECUTION = "lgbt_persecution"


class PersecutionClassification(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    id: int | None = None
    person_id: int
    status: PersecutionClassificationStatus
    confidence: float = Field(ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)
    evidence_types: list[PersecutionEvidenceType] = Field(default_factory=list)
    classifier_name: str
    classifier_version: str
    classified_at: datetime | None = None


class PersecutionClassifier(Protocol):
    classifier_name: str
    classifier_version: str

    def classify(
        self,
        *,
        person_id: int,
        events: Sequence[dict[str, Any]],
        articles: Sequence[dict[str, Any]],
    ) -> PersecutionClassification: ...
