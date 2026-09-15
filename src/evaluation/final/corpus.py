"""Final evaluation corpus schema: scenarios with human ground truth per stage."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

FINAL_EVALUATION_DATASET_VERSION = "final-eval-v1"
DEFAULT_CORPUS_PATH = (
    Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "final_evaluation" / "corpus.json"
)

# False statements about a real person that the safety gates count.
DANGEROUS_KINDS = frozenset(
    {
        "false_person_link",
        "false_political_classification",
        "false_rf_not_matched",
        "false_actionable_candidate",
        "unsupported_report_claim",
    }
)

RF_NO_SNAPSHOT = "no_snapshot"
RF_NO_MATCH_RECORD = "no_match_record"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FinalArticle(_Strict):
    external_id: str
    title: str
    text: str


class SurfaceRef(_Strict):
    """A person mention as a human reads it: exact text span in one article."""

    article: str
    surface: str


class FinalIdentity(_Strict):
    """One real-world person in the scenario."""

    mentions: list[SurfaceRef] = Field(default_factory=list)
    # Index into `FinalCase.seed_persons` when this person already existed.
    seed_index: int | None = None
    # Accepted latest classification statuses; None = not evaluated.
    persecution: list[str] | None = None
    # matched | not_matched | ambiguous | needs_review | insufficient_data |
    # no_match_record | no_snapshot; None = not evaluated.
    rf_status: str | None = None
    candidate: bool | None = None
    # Whether ER must leave this person's mentions pending review.
    er_review: bool | None = None


class FinalRunExpectation(_Strict):
    documents_ingested: int | None = None
    documents_skipped: int | None = None
    findings_created: int | None = None


class FinalResearchCheck(_Strict):
    query: str
    # What request intake is expected to produce (a fake LLM returns it).
    request: dict[str, Any]
    expected_identities: list[str]
    # Articles the report must cite for the returned persons.
    expected_evidence_articles: list[str] = Field(default_factory=list)


class FinalCase(_Strict):
    id: str
    categories: list[str]
    description: str
    articles: list[FinalArticle]
    # External ids published before each monitoring run; default: one run, all.
    runs: list[list[str]] | None = None
    # (full_name, birth date dd.mm.yyyy); None = no Rosfinmonitoring snapshot.
    rf_snapshot: list[tuple[str, str]] | None = None
    seed_persons: list[str] = Field(default_factory=list)
    expected_extraction: dict[str, list[str]] = Field(default_factory=dict)
    identities: dict[str, FinalIdentity] = Field(default_factory=dict)
    run_expectations: list[FinalRunExpectation] = Field(default_factory=list)
    research: list[FinalResearchCheck] = Field(default_factory=list)
    review_required: bool | None = None
    requires_semantic_models: bool = False
    # A documented limitation this case demonstrates; it is still scored and
    # reported. Only the dangerous kinds listed in `known_limitation_kinds` are
    # excluded from the hard safety gates — any other dangerous error still fails.
    known_limitation: str | None = None
    known_limitation_kinds: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _references_exist(self) -> FinalCase:
        if self.known_limitation_kinds and self.known_limitation is None:
            raise ValueError(f"{self.id}: known_limitation_kinds without known_limitation")
        unknown_kinds = set(self.known_limitation_kinds) - DANGEROUS_KINDS
        if unknown_kinds:
            raise ValueError(f"{self.id}: unknown known_limitation_kinds {sorted(unknown_kinds)}")
        article_ids = {article.external_id for article in self.articles}
        for run in self.runs or []:
            unknown = set(run) - article_ids
            if unknown:
                raise ValueError(f"{self.id}: runs reference unknown articles {sorted(unknown)}")
        for key, identity in self.identities.items():
            for mention in identity.mentions:
                if mention.article not in article_ids:
                    raise ValueError(f"{self.id}/{key}: unknown article {mention.article}")
                text = next(a.text for a in self.articles if a.external_id == mention.article)
                if mention.surface not in text:
                    raise ValueError(f"{self.id}/{key}: {mention.surface!r} not in article text")
            if identity.seed_index is not None and not (
                0 <= identity.seed_index < len(self.seed_persons)
            ):
                raise ValueError(f"{self.id}/{key}: seed_index out of range")
        for check in self.research:
            unknown_keys = set(check.expected_identities) - set(self.identities)
            if unknown_keys:
                raise ValueError(f"{self.id}: research expects unknown {sorted(unknown_keys)}")
        return self

    @property
    def run_plan(self) -> list[list[str]]:
        return self.runs or [[article.external_id for article in self.articles]]


class FinalCorpus(_Strict):
    dataset_version: str
    cases: list[FinalCase]

    @model_validator(mode="after")
    def _unique_ids(self) -> FinalCorpus:
        ids = [case.id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("case ids must be unique")
        return self


def is_gated(case: FinalCase, kind: str) -> bool:
    """Whether a dangerous error of `kind` in `case` counts towards the safety gates."""
    return kind not in case.known_limitation_kinds


def load_final_corpus(path: Path = DEFAULT_CORPUS_PATH) -> FinalCorpus:
    return FinalCorpus.model_validate(json.loads(path.read_text(encoding="utf-8")))
