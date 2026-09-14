"""Aggregate case results into metrics, safety gates and JSON/Markdown reports."""

from __future__ import annotations

import os
import subprocess
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from evaluation.final.corpus import FinalCorpus
from evaluation.final.models import CaseResult, Counts, FinalEvaluationReport, GateResult
from extraction.events import RuleBasedEventExtractor
from extraction.extractors import RuleBasedEntityExtractor
from extraction.normalizers import RuleBasedMentionNormalizer
from persecution.classifier import RuleBasedPersecutionClassifier
from persons.resolution.service import RESOLVER_VERSION
from research.reports.models import RESEARCH_REPORT_VERSION
from rosfinmonitoring.matcher import RuleBasedRosfinmonitoringMatcher
from semantic_retrieval.documents import (
    EVENT_REPRESENTATION_VERSION,
    PERSON_REPRESENTATION_VERSION,
)

# Hard safety gates: a false statement about a real person is never acceptable.
MAX_FALSE_PERSON_LINKS = 0
MAX_FALSE_RF_NOT_MATCHED = 0
MAX_UNSUPPORTED_REPORT_CLAIMS = 0
MAX_FALSE_ACTIONABLE_CANDIDATES = 0
# Quality floors, deliberately below the current baseline (synthetic corpus):
# they catch regressions without pretending the rules are perfect.
# Baseline final-eval-v1: actionable precision 1.0, candidate recall 0.60,
# persecution accuracy 0.55, extraction person F1 0.86, research precision 1.0,
# fully correct cases 0.41.
MIN_ACTIONABLE_CANDIDATE_PRECISION = 0.9
MIN_RESEARCH_PRECISION = 0.9
MIN_CANDIDATE_RECALL = 0.5
MIN_PERSECUTION_ACCURACY = 0.5
MIN_EXTRACTION_PERSON_F1 = 0.8
MIN_FULLY_CORRECT_CASE_RATE = 0.35

KNOWN_SYSTEM_LIMITATIONS = [
    "Rule-based persecution classifier (keyword windows), no NLP.",
    "Rosfinmonitoring matching is name-based; birth dates are not extracted for persons.",
    "ER has no context features: a single existing namesake is auto-linked.",
    (
        "The extraction normalizer mangles some female names and captures titles "
        "(e.g. «Судья Мария»); surname-only mentions are not extracted."
    ),
    "Semantic retrieval cases need real embedding models and are skipped in the baseline.",
    "Synthetic corpus: metrics measure regressions and safety, not real-world accuracy.",
]


def component_versions() -> dict[str, str]:
    extractor = RuleBasedEntityExtractor()
    classifier = RuleBasedPersecutionClassifier()
    return {
        "extractor": f"{extractor.extractor_name}@{extractor.extractor_version}",
        "event_extractor": (
            f"{RuleBasedEventExtractor.extractor_name}@{RuleBasedEventExtractor.extractor_version}"
        ),
        "normalizer": f"rule-based@{RuleBasedMentionNormalizer.normalizer_version}",
        "entity_resolution": RESOLVER_VERSION,
        "persecution_classifier": (f"{classifier.classifier_name}@{classifier.classifier_version}"),
        "rosfinmonitoring_matcher": (
            f"{RuleBasedRosfinmonitoringMatcher.matcher_name}"
            f"@{RuleBasedRosfinmonitoringMatcher.matcher_version}"
        ),
        "semantic_representation": (
            f"person@{PERSON_REPRESENTATION_VERSION},event@{EVENT_REPRESENTATION_VERSION}"
        ),
        "research_report": RESEARCH_REPORT_VERSION,
    }


def code_commit() -> str:
    if os.environ.get("GITHUB_SHA"):
        return os.environ["GITHUB_SHA"]
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, timeout=5
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], capture_output=True, text=True, check=True, timeout=5
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return f"{sha}{'-dirty' if dirty else ''}"


def _rate(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else round(numerator / denominator, 4)


def build_report(
    corpus: FinalCorpus, cases: Sequence[CaseResult], *, generated_at: datetime | None = None
) -> FinalEvaluationReport:
    evaluated = [case for case in cases if case.skipped_reason is None]
    extraction = Counts()
    candidates = Counts()
    actionable = Counts()
    research = Counts()
    for case in evaluated:
        extraction.merge(case.extraction)
        candidates.merge(case.candidates)
        actionable.merge(case.actionable)
        for outcome in case.research:
            research.merge(outcome.counts)

    auto_links = sum(case.er.auto_links for case in evaluated)
    auto_links_correct = sum(case.er.auto_links_correct for case in evaluated)
    persecution_pairs = [pair for case in evaluated for pair in case.persecution]
    persecution_correct = sum(1 for accepted, actual in persecution_pairs if actual in accepted)
    political = Counts()
    for accepted, actual in persecution_pairs:
        political.add(expected="political" in accepted, actual=actual == "political")
    rf_pairs = [pair for case in evaluated for pair in case.rf]
    rf_correct = sum(1 for expected, actual in rf_pairs if expected == actual)
    claims = sum(outcome.claims for case in evaluated for outcome in case.research)
    fully_correct = sum(1 for case in evaluated if case.fully_correct)
    review_cases = sum(1 for case in evaluated if case.review_required)

    false_positives = [fp for case in evaluated for fp in case.false_positives]
    by_kind = Counter(fp.kind for fp in false_positives)
    gated_by_kind = Counter(fp.kind for fp in false_positives if fp.gated)

    persecution_accuracy = _rate(persecution_correct, len(persecution_pairs))
    fully_correct_case_rate = _rate(fully_correct, len(evaluated))
    metrics: dict[str, object] = {
        "extraction_person": extraction.summary(),
        "er_auto_link_precision": _rate(auto_links_correct, auto_links),
        "er_auto_links": auto_links,
        "er_false_link_count": sum(case.er.false_links for case in evaluated),
        "er_pending_reviews": sum(case.er.reviews for case in evaluated),
        "persecution_accuracy": persecution_accuracy,
        "persecution_political": political.summary(),
        "rf_status_accuracy": _rate(rf_correct, len(rf_pairs)),
        "candidates": candidates.summary(),
        "actionable_candidates": actionable.summary(),
        "actionable_candidate_precision": actionable.precision,
        "research": research.summary(),
        "report_claims": claims,
        "review_rate": _rate(review_cases, len(evaluated)),
        "fully_correct_case_rate": fully_correct_case_rate,
    }

    def gate(name: str, value: float | None, threshold: str, passed: bool) -> GateResult:
        return GateResult(name=name, value=value, threshold=threshold, passed=passed)

    def at_least(value: float | None, minimum: float) -> bool:
        return value is not None and value >= minimum

    gates = [
        gate(
            "false_person_links",
            gated_by_kind["false_person_link"],
            f"<= {MAX_FALSE_PERSON_LINKS}",
            gated_by_kind["false_person_link"] <= MAX_FALSE_PERSON_LINKS,
        ),
        gate(
            "false_rf_not_matched",
            gated_by_kind["false_rf_not_matched"],
            f"<= {MAX_FALSE_RF_NOT_MATCHED}",
            gated_by_kind["false_rf_not_matched"] <= MAX_FALSE_RF_NOT_MATCHED,
        ),
        gate(
            "unsupported_report_claims",
            gated_by_kind["unsupported_report_claim"],
            f"<= {MAX_UNSUPPORTED_REPORT_CLAIMS}",
            gated_by_kind["unsupported_report_claim"] <= MAX_UNSUPPORTED_REPORT_CLAIMS,
        ),
        gate(
            "false_actionable_candidates",
            gated_by_kind["false_actionable_candidate"],
            f"<= {MAX_FALSE_ACTIONABLE_CANDIDATES}",
            gated_by_kind["false_actionable_candidate"] <= MAX_FALSE_ACTIONABLE_CANDIDATES,
        ),
        gate(
            "actionable_candidate_precision",
            actionable.precision,
            f">= {MIN_ACTIONABLE_CANDIDATE_PRECISION}",
            at_least(actionable.precision, MIN_ACTIONABLE_CANDIDATE_PRECISION),
        ),
        gate(
            "research_precision",
            research.precision,
            f">= {MIN_RESEARCH_PRECISION}",
            at_least(research.precision, MIN_RESEARCH_PRECISION),
        ),
        gate(
            "candidate_recall",
            candidates.recall,
            f">= {MIN_CANDIDATE_RECALL}",
            at_least(candidates.recall, MIN_CANDIDATE_RECALL),
        ),
        gate(
            "persecution_accuracy",
            persecution_accuracy,
            f">= {MIN_PERSECUTION_ACCURACY}",
            at_least(persecution_accuracy, MIN_PERSECUTION_ACCURACY),
        ),
        gate(
            "extraction_person_f1",
            extraction.f1,
            f">= {MIN_EXTRACTION_PERSON_F1}",
            at_least(extraction.f1, MIN_EXTRACTION_PERSON_F1),
        ),
        gate(
            "fully_correct_case_rate",
            fully_correct_case_rate,
            f">= {MIN_FULLY_CORRECT_CASE_RATE}",
            at_least(fully_correct_case_rate, MIN_FULLY_CORRECT_CASE_RATE),
        ),
    ]

    failure_categories: Counter[str] = Counter()
    for case in evaluated:
        for check in case.failed_checks:
            stage = check.name.split("[")[0].split(".")[0]
            if "." in check.name:
                stage = f"{stage}.{check.name.rsplit('.', 1)[1]}"
            failure_categories[stage] += 1

    return FinalEvaluationReport(
        dataset_version=corpus.dataset_version,
        code_commit=code_commit(),
        generated_at=generated_at or datetime.now(UTC),
        component_versions=component_versions(),
        cases_total=len(cases),
        cases_evaluated=len(evaluated),
        cases_skipped=[
            {"case_id": case.case_id, "reason": case.skipped_reason or ""}
            for case in cases
            if case.skipped_reason is not None
        ],
        metrics=metrics,
        false_positives={
            "counts": {
                kind: by_kind[kind]
                for kind in (
                    "false_person_link",
                    "false_political_classification",
                    "false_rf_not_matched",
                    "false_actionable_candidate",
                    "unsupported_report_claim",
                )
            },
            "gated_counts": dict(gated_by_kind),
            "items": [fp.model_dump() for fp in false_positives],
        },
        gates=gates,
        gates_passed=all(item.passed for item in gates),
        failure_categories=dict(sorted(failure_categories.items())),
        review_outcomes={
            "cases_requiring_review": review_cases,
            "pending_er_reviews": metrics["er_pending_reviews"],
            "reports_requiring_review": sum(
                1 for case in evaluated for outcome in case.research if outcome.review_required
            ),
        },
        known_limitations=[
            {"case_id": case.case_id, "limitation": case.known_limitation}
            for case in cases
            if case.known_limitation
        ]
        + [{"case_id": "*", "limitation": text} for text in KNOWN_SYSTEM_LIMITATIONS],
        cases=list(cases),
    )


def deterministic_view(report: FinalEvaluationReport) -> dict[str, object]:
    """Everything that must be identical between two runs on the same commit."""
    data = report.model_dump(mode="json")
    for volatile in ("generated_at", "code_commit"):
        data.pop(volatile)
    return data


def render_markdown(report: FinalEvaluationReport) -> str:
    metrics = report.metrics
    lines = [
        "# Final system evaluation",
        "",
        f"- Dataset: `{report.dataset_version}`",
        f"- Commit: `{report.code_commit}`",
        f"- Generated: {report.generated_at.isoformat()}",
        f"- Cases: {report.cases_evaluated} evaluated, {len(report.cases_skipped)} skipped",
        f"- Gates: **{'PASSED' if report.gates_passed else 'FAILED'}**",
        "",
        "## Safety gates",
        "",
        "| Gate | Value | Threshold | Result |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| {gate.name} | {gate.value} | {gate.threshold} | {'pass' if gate.passed else 'FAIL'} |"
        for gate in report.gates
    ]
    lines += ["", "## Metrics", "", "| Metric | Value |", "|---|---|"]
    for name, value in metrics.items():
        lines.append(f"| {name} | {value} |")
    lines += ["", "## Dangerous errors (false positives)", ""]
    counts = report.false_positives["counts"]
    assert isinstance(counts, dict)
    lines += [f"- {kind}: {count}" for kind, count in counts.items()]
    items = report.false_positives["items"]
    assert isinstance(items, list)
    for item in items:
        gated = "" if item["gated"] else " (known limitation, not gated)"
        lines.append(
            f"  - `{item['case_id']}` {item['kind']} {item['identity'] or ''}: "
            f"{item['detail']}{gated}"
        )
    lines += ["", "## Failed checks by case", ""]
    for case in report.cases:
        if case.skipped_reason:
            lines.append(f"- `{case.case_id}`: skipped — {case.skipped_reason}")
            continue
        failed = case.failed_checks
        if failed:
            lines.append(f"- `{case.case_id}`:")
            lines += [f"  - {check.name}: {check.detail}" for check in failed]
    lines += ["", "## Component versions", ""]
    lines += [f"- {name}: `{version}`" for name, version in report.component_versions.items()]
    lines += ["", "## Known limitations", ""]
    lines += [f"- `{item['case_id']}`: {item['limitation']}" for item in report.known_limitations]
    return "\n".join(lines) + "\n"


def write_reports(report: FinalEvaluationReport, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "final_evaluation.json"
    markdown_path = output_dir / "final_evaluation.md"
    json_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, markdown_path
