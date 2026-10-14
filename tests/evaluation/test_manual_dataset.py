"""The set that ships with the repository: it loads, it has the shape, it is invented.

The measured set lives outside the repository and names real people; this one has to be
runnable by anyone after a clone, and it must never be the place a real name ends up.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from evaluation.investigation.__main__ import DATA
from evaluation.investigation.manual import load

VALID_FACTS = {"charges", "role", "politics", "rf_list", "latest_news"}


@pytest.fixture(scope="module")
def document() -> dict[str, Any]:
    return load(DATA)


def test_the_shipped_set_is_where_the_command_looks_for_it() -> None:
    assert DATA.is_file()
    assert DATA.name == "manual_golden.json"


def test_the_set_holds_the_kinds_the_benchmark_asks_for(document: dict[str, Any]) -> None:
    assert len(document["cases"]) >= 20
    assert len(document["unnamed_cases"]) >= 5
    assert len(document["namesake_pairs"]) >= 3


def test_every_case_can_be_asked_and_every_expectation_is_one_of_three_shapes(
    document: dict[str, Any],
) -> None:
    for case in document["cases"]:
        assert case["case_id"] and case["query"], case
        assert case["expected"], case["case_id"]
        for entry in case["expected"]:
            if entry.get("kind"):
                assert entry["kind"] == "not_established", entry
                continue
            assert entry["fact"] in VALID_FACTS, entry
            assert entry["value"], entry
            # An expectation nobody can check is worse than no expectation at all.
            assert entry.get("parts") or entry.get("source"), entry


def test_the_person_cases_cover_the_names_the_run_has_to_get_right(
    document: dict[str, Any],
) -> None:
    queries = [str(case["query"]) for case in document["cases"]]
    groups = {str(case["group"]) for case in document["cases"]}

    # A name in another case, a name in another order, and a rare one.
    assert any(len(query.split()) == 2 for query in queries)
    assert any(len(query.split()) == 1 for query in queries)
    assert {"namesake_collision", "rare_surname"} <= groups


def test_the_unnamed_cases_point_at_a_publication_of_the_database(
    document: dict[str, Any],
) -> None:
    for case in document["unnamed_cases"]:
        assert isinstance(case["article_id"], int), case
        assert isinstance(case["name_verified"], bool), case
    # A publication that names nobody is the rule; one that names somebody is the case that
    # has to be proved, and none of these claims to be one.
    assert not any(case["name_verified"] for case in document["unnamed_cases"])


def test_no_real_person_is_named_in_the_shipped_set() -> None:
    """The file is public and this is the test that says so.

    Every person case here is invented, so every expectation is «не установлено» — there
    is nothing to publish. The measured set has real names and articles and lives in
    `var/`, which is gitignored; see `data/README.md`.
    """
    document = json.loads(DATA.read_text(encoding="utf-8"))

    assert document["synthetic"] is True
    for case in document["cases"]:
        for entry in case["expected"]:
            assert entry.get("kind") == "not_established", (case["case_id"], entry)
    for pair in document["namesake_pairs"]:
        for person in pair["people"]:
            assert person["in_db_charges"] is None, (pair["case_id"], person)


def test_the_set_is_valid_json_and_ends_with_a_newline() -> None:
    raw = DATA.read_text(encoding="utf-8")

    assert raw.endswith("\n")
    assert json.loads(raw)
    assert "TAVILY_API_KEY" not in raw
    assert "sk-" not in raw


def test_a_path_outside_the_repository_is_still_readable() -> None:
    outside = Path("var/investigation_eval/manual_golden.json")

    if not outside.is_file():
        pytest.skip("the annotated set is not on this machine")
    assert load(outside)["cases"]
