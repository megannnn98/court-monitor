"""The benchmark keeps false findings as evidence for inspection and counts them safely."""

from typing import Any

from evaluation.investigation.web_benchmark import as_markdown, summarise


def test_a_false_finding_is_counted_without_losing_its_per_case_value() -> None:
    report: dict[str, Any] = {
        "mode": "local+telegram",
        "cases": [
            {
                "kind": "person",
                "subject_correct": True,
                "facts_recall": 1.0,
                "facts_precision": 0.5,
                "false_facts": ["charges=ст. 360 УК РФ"],
                "status": "partial",
            }
        ],
    }

    summary = summarise(report)

    assert report["cases"][0]["false_facts"] == ["charges=ст. 360 УК РФ"]
    assert summary["false_facts"] == 1
    assert "| local+telegram |" in as_markdown([report])
