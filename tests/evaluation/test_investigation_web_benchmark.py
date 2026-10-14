"""The benchmark keeps false findings as evidence for inspection and counts them safely,
and it writes down what a case cost without writing down who was asked about."""

import asyncio
from typing import Any

from evaluation.investigation.web_benchmark import (
    CountingSearch,
    as_markdown,
    canonical,
    credits_per_search,
    summarise,
)


class _Search:
    search_depth = "advanced"

    source = "tavily"
    title = "Tavily"

    async def search(self, query: Any) -> list[Any]:
        return []


class _Query:
    text = "Мария Бонцлер статья"


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


def test_a_search_of_the_depth_the_run_asked_for_is_priced_at_that_depth() -> None:
    assert credits_per_search("basic") == 1
    assert credits_per_search("advanced") == 2
    assert credits_per_search("ultra-fast") == 1

    counted = CountingSearch(_Search())

    asyncio.run(counted.search(_Query()))

    assert counted.requests == 1
    assert counted.since((0, 0, 0))["tavily_credits"] == 2


def test_the_benchmark_asks_the_same_question_about_a_page_which_page_it_is() -> None:
    """The run's own key decides which page an item is. The measurement has to ask the
    same question, or `?id=2` is counted as the article the database already holds."""
    assert canonical("https://news.example.test/news.php?id=2") == (
        "news.example.test/news.php?id=2"
    )
    assert canonical("https://news.example.test/news.php?id=1") != (
        "news.example.test/news.php?id=2"
    )
    # The same page as it is written with a campaign on it is still that page.
    assert canonical("https://news.example.test/a?utm_source=tg") == "news.example.test/a"


def test_a_report_holds_the_hash_and_the_length_of_a_query_but_never_the_query() -> None:
    counted = CountingSearch(_Search())

    asyncio.run(counted.search(_Query()))
    (batch,) = counted.batches

    written = str(batch)
    assert "Бонцлер" not in written
    assert batch["query_length"] == len("Мария Бонцлер статья")
    assert batch["query_hash"]
    assert "query" not in {key for key in batch if key.endswith("query")}
