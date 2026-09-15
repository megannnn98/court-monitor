"""Per-case results, metrics and the final evaluation report."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class Counts(BaseModel):
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0

    def add(self, *, expected: bool, actual: bool) -> None:
        if expected and actual:
            self.tp += 1
        elif actual:
            self.fp += 1
        elif expected:
            self.fn += 1
        else:
            self.tn += 1

    def merge(self, other: Counts) -> None:
        self.tp += other.tp
        self.fp += other.fp
        self.fn += other.fn
        self.tn += other.tn

    @property
    def precision(self) -> float | None:
        return None if self.tp + self.fp == 0 else round(self.tp / (self.tp + self.fp), 4)

    @property
    def recall(self) -> float | None:
        return None if self.tp + self.fn == 0 else round(self.tp / (self.tp + self.fn), 4)

    @property
    def f1(self) -> float | None:
        precision, recall = self.precision, self.recall
        if not precision or not recall:
            return None if precision is None or recall is None else 0.0
        return round(2 * precision * recall / (precision + recall), 4)

    def summary(self) -> dict[str, float | int | None]:
        return {
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
        }


class ErCounts(BaseModel):
    auto_links: int = 0
    auto_links_correct: int = 0
    false_links: int = 0
    reviews: int = 0


class FalsePositive(BaseModel):
    """A dangerous error: a false statement about a real person."""

    kind: str
    case_id: str
    identity: str | None
    detail: str
    # False for cases that document a known limitation: still reported.
    gated: bool = True


class ResearchOutcome(BaseModel):
    query: str
    status: str
    error: str | None = None
    person_ids: list[int] = Field(default_factory=list)
    report_status: str | None = None
    review_required: bool = False
    claims: int = 0
    provenance_problems: list[str] = Field(default_factory=list)
    cited_articles: set[str] = Field(default_factory=set)
    counts: Counts = Field(default_factory=Counts)


class CheckResult(BaseModel):
    name: str
    passed: bool
    detail: str | None = None


class CaseResult(BaseModel):
    case_id: str
    categories: list[str]
    known_limitation: str | None = None
    skipped_reason: str | None = None
    checks: list[CheckResult] = Field(default_factory=list)
    extraction: Counts = Field(default_factory=Counts)
    er: ErCounts = Field(default_factory=ErCounts)
    # (accepted statuses, actual)
    persecution: list[tuple[list[str], str | None]] = Field(default_factory=list)
    rf: list[tuple[str, str | None]] = Field(default_factory=list)
    candidates: Counts = Field(default_factory=Counts)
    actionable: Counts = Field(default_factory=Counts)
    research: list[ResearchOutcome] = Field(default_factory=list)
    review_required: bool = False
    false_positives: list[FalsePositive] = Field(default_factory=list)

    def check(self, name: str, passed: bool, detail: str) -> None:
        self.checks.append(CheckResult(name=name, passed=passed, detail=None if passed else detail))

    @property
    def fully_correct(self) -> bool:
        return self.skipped_reason is None and all(check.passed for check in self.checks)

    @property
    def failed_checks(self) -> list[CheckResult]:
        return [check for check in self.checks if not check.passed]


class GateResult(BaseModel):
    name: str
    value: float | int | None
    threshold: str
    passed: bool


class FinalEvaluationReport(BaseModel):
    dataset_version: str
    code_commit: str
    generated_at: datetime
    component_versions: dict[str, str]
    cases_total: int
    cases_evaluated: int
    cases_skipped: list[dict[str, str]]
    metrics: dict[str, object]
    false_positives: dict[str, object]
    gates: list[GateResult]
    gates_passed: bool
    failure_categories: dict[str, int]
    review_outcomes: dict[str, object]
    known_limitations: list[dict[str, str]]
    cases: list[CaseResult]
