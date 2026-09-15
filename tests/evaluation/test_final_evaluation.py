"""Final system evaluation on PostgreSQL: safety gates, quality floors, determinism."""

from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from evaluation.final.corpus import FinalCorpus, load_final_corpus
from evaluation.final.report import build_report, deterministic_view, render_markdown
from evaluation.final.runner import FinalEvaluationRunner


def test_final_evaluation_passes_safety_gates_and_quality_floors(
    session_factory: sessionmaker[Session],
) -> None:
    corpus = load_final_corpus()
    runner = FinalEvaluationRunner(session_factory.kw["bind"])

    report = build_report(corpus, runner.run(corpus))

    failed = [gate for gate in report.gates if not gate.passed]
    assert not failed, [(gate.name, gate.value, gate.threshold) for gate in failed]
    counts = report.false_positives["gated_counts"]
    assert isinstance(counts, dict)
    for dangerous in (
        "false_person_link",
        "false_rf_not_matched",
        "false_actionable_candidate",
        "unsupported_report_claim",
    ):
        assert counts.get(dangerous, 0) == 0, dangerous
    assert report.cases_evaluated >= 40
    assert "Safety gates" in render_markdown(report)


def test_final_evaluation_is_deterministic(session_factory: sessionmaker[Session]) -> None:
    full = load_final_corpus()
    subset = FinalCorpus(
        dataset_version=full.dataset_version,
        cases=[
            case
            for case in full.cases
            if case.id
            in {
                "clear_positive_antiwar_detention",
                "er_review_two_existing_namesakes",
                "multiple_persons_one_case",
                "new_article_second_run",
                "same_person_three_articles",
            }
        ],
    )
    runner = FinalEvaluationRunner(session_factory.kw["bind"])

    first = deterministic_view(build_report(subset, runner.run(subset)))
    second = deterministic_view(build_report(subset, runner.run(subset)))

    assert first == second
