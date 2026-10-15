"""The relevance holdout: a frozen stream of new articles, labelled by hand, built and
labelled without any model."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from evaluation.relevance.annotate_holdout import annotate, load_labels
from evaluation.relevance.holdout import (
    KINDS,
    START_AT,
    FrozenError,
    build,
    normalize,
    ordered,
    read_jsonl,
    text_hash,
    write_frozen,
)

CREATED = datetime(2026, 10, 15, 12, tzinfo=UTC)


def row(article_id: int, when: str, text: str = "", title: str = "") -> dict[str, Any]:
    return {
        "article_id": article_id,
        "title": title or f"Заголовок {article_id}",
        "text": text or f"Текст статьи {article_id}",
        "published_at": when,
        "source": "Медиазона",
        "url": f"https://example.org/{article_id}",
    }


def old(
    article_id: int, text: str, title: str = "старый", file: str = "train.jsonl"
) -> dict[str, Any]:
    return {"article_id": article_id, "title": title, "text": text, "file": file}


def built(rows: list[dict[str, Any]], old_rows: list[dict[str, Any]] | None = None) -> Any:
    return build(rows, old_rows or [], created_at=CREATED, git_commit="abc123")


# --- window and order ----------------------------------------------------------------


def test_articles_before_the_cutoff_stay_out() -> None:
    articles, metadata, _ = built(
        [
            row(1, "2026-09-16T23:59:59+00:00"),
            row(2, "2026-09-17T00:00:00+00:00"),
            row(3, "2026-09-17T02:59:59+03:00"),  # 23:59:59 UTC the day before
            row(4, "2026-10-03T11:25:02+00:00"),
        ]
    )
    assert [a["article_id"] for a in articles] == [2, 4]
    assert metadata["start_at"] == "2026-09-17T00:00:00Z"
    assert metadata["end_at"] == "2026-10-03T11:25:02Z"
    assert metadata["first_published_at"] == "2026-09-17T00:00:00Z"


def test_the_order_is_by_time_then_id_whatever_the_input() -> None:
    rows = [
        row(5, "2026-09-20T10:00:00+00:00"),
        row(3, "2026-09-20T10:00:00+00:00"),
        row(9, "2026-09-18T10:00:00+00:00"),
    ]
    first, _, _ = built(rows)
    second, _, _ = built(list(reversed(rows)))
    assert [a["article_id"] for a in first] == [9, 3, 5]
    assert first == second
    assert [r["article_id"] for r in ordered(rows)] == [9, 3, 5]


def test_articles_without_text_are_counted_and_left_out() -> None:
    rows = [
        row(1, "2026-09-18T00:00:00+00:00"),
        {**row(2, "2026-09-18T00:00:00+00:00"), "text": "  "},
    ]
    articles, metadata, _ = built(rows)
    assert [a["article_id"] for a in articles] == [1]
    assert metadata["articles_without_text"] == 1


def test_an_article_keeps_what_annotation_needs() -> None:
    (article,), _, _ = built([row(1, "2026-09-18T05:00:00+03:00")])
    assert article == {
        "article_id": 1,
        "title": "Заголовок 1",
        "text": "Текст статьи 1",
        "source": "Медиазона",
        "url": "https://example.org/1",
        "published_at": "2026-09-18T02:00:00Z",
        "text_hash": text_hash("Текст статьи 1"),
    }


# --- leakage -------------------------------------------------------------------------


def test_normalization_folds_form_space_and_case() -> None:
    assert normalize("  Суд АРЕСТОВАЛ\n\nактивиста ") == "суд арестовал активиста"
    assert normalize("\u0439") == normalize("\u0438\u0306")  # «й» composed or not
    assert text_hash("Суд  арестовал") == text_hash("суд арестовал")
    assert text_hash("Суд арестовал") != text_hash("Суд освободил")


def test_text_duplicates_of_the_earlier_data_are_left_out_and_counted_apart() -> None:
    rows = [
        row(1, "2026-09-18T00:00:00+00:00", text="Суд арестовал активиста"),
        row(2, "2026-09-18T01:00:00+00:00", text="СУД  арестовал\nактивиста"),
        row(3, "2026-09-18T02:00:00+00:00", text="Совсем новый текст"),
    ]
    articles, metadata, report = built(rows, [old(77, "Суд арестовал активиста")])
    assert [a["article_id"] for a in articles] == [3]
    assert [m["article_id"] for m in report["exact_text_duplicates"]] == [1]
    assert [m["article_id"] for m in report["normalized_text_hash_duplicates"]] == [1, 2]
    assert report["normalized_text_hash_duplicates"][0]["old"] == [
        {"article_id": 77, "file": "train.jsonl"}
    ]
    assert metadata["excluded_normalized_text_hash_duplicates"] == 2
    assert metadata["of_them_exact_text_duplicates"] == 1
    assert metadata["of_them_normalized_only"] == 1
    assert metadata["article_count"] == 1
    assert metadata["found_in_window"] == 3


def test_an_article_id_seen_before_is_reported_not_removed() -> None:
    articles, _, report = built(
        [row(5, "2026-09-18T00:00:00+00:00", text="Новый текст")],
        [old(5, "Другой текст старой базы", file="var/junk_screen/sample.jsonl")],
    )
    assert [a["article_id"] for a in articles] == [5]
    assert report["article_id_duplicates"] == [
        {"article_id": 5, "old": [{"article_id": 5, "file": "var/junk_screen/sample.jsonl"}]}
    ]


def test_a_title_seen_before_is_reported_not_removed() -> None:
    articles, metadata, report = built(
        [row(1, "2026-09-18T00:00:00+00:00", title="🌒 Главные новости к вечеру")],
        [old(9, "иной текст", title="🌒 Главные новости к вечеру")],
    )
    assert [a["article_id"] for a in articles] == [1]
    assert [m["article_id"] for m in report["title_duplicates"]] == [1]
    assert metadata["title_duplicates_reported"] == 1
    assert report["counts"]["title_duplicates"] == 1


def test_repeats_inside_the_holdout_are_reported_and_kept() -> None:
    rows = [
        row(1, "2026-09-18T00:00:00+00:00", text="Один пост"),
        row(2, "2026-09-18T01:00:00+00:00", text="один  пост"),
    ]
    articles, _, report = built(rows)
    assert len(articles) == 2
    assert report["within_holdout_normalized_duplicates"] == [{"article_ids": [1, 2]}]


# --- metadata and freezing -----------------------------------------------------------


def test_metadata_fixes_the_window_and_the_rules() -> None:
    _, metadata, _ = built(
        [row(1, "2026-09-20T03:13:28+00:00"), row(2, "2026-10-03T11:25:02+00:00")],
        [old(7, "x", file="data/ml/relevance/test.jsonl")],
    )
    assert metadata == metadata | {
        "dataset": "relevance-holdout-v1",
        "start_at": "2026-09-17T00:00:00Z",
        "end_at": "2026-10-03T11:25:02Z",
        "first_published_at": "2026-09-20T03:13:28Z",
        "created_at": "2026-10-15T12:00:00Z",
        "rubric": "junk-labels-v1",
        "selection": "consecutive temporal stream",
        "model_predictions_used_for_selection": False,
        "model_predictions_used_for_annotation": False,
        "git_commit": "abc123",
        "frozen": True,
        "article_count": 2,
        "old_data": ["data/ml/relevance/test.jsonl"],
    }
    assert START_AT == datetime(2026, 9, 17, tzinfo=UTC)


def test_an_empty_window_is_refused() -> None:
    with pytest.raises(ValueError, match="no articles"):
        built([row(1, "2026-09-01T00:00:00+00:00")])


def test_a_frozen_holdout_is_never_rewritten(tmp_path: Path) -> None:
    articles, metadata, report = built([row(1, "2026-09-18T00:00:00+00:00")])
    write_frozen(tmp_path, articles, metadata, report)
    assert read_jsonl(tmp_path / "articles.jsonl") == articles
    assert json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8")) == metadata
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    other, other_metadata, other_report = built([row(2, "2026-09-19T00:00:00+00:00")])
    with pytest.raises(FrozenError, match="frozen"):
        write_frozen(tmp_path, other, other_metadata, other_report)
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_one_file_of_a_holdout_is_enough_to_refuse(tmp_path: Path) -> None:
    (tmp_path / "leakage_report.json").write_text("{}", encoding="utf-8")
    articles, metadata, report = built([row(1, "2026-09-18T00:00:00+00:00")])
    with pytest.raises(FrozenError):
        write_frozen(tmp_path, articles, metadata, report)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["leakage_report.json"]


# --- no model ------------------------------------------------------------------------


def test_the_holdout_modules_import_no_model() -> None:
    code = (
        "import sys\n"
        "import evaluation.relevance.holdout, evaluation.relevance.build_holdout, "
        "evaluation.relevance.annotate_holdout, evaluation.relevance.sample_holdout\n"
        "bad = [m for m in sys.modules if m.startswith(('sklearn', 'numpy', 'pymorphy3')) "
        "or m in ('evaluation.relevance.baseline', 'evaluation.relevance.preprocessing', "
        "'evaluation.relevance.ablation', 'evaluation.relevance.analysis', "
        "'evaluation.junk_screen.labels', 'llm.chat')]\n"
        "print(','.join(bad))\n"
    )
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        cwd=root,
        env={"PYTHONPATH": str(root / "src")},
    )
    assert result.stdout.strip() == ""


def test_building_and_labelling_call_no_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pytest.importorskip("sklearn")
    from evaluation.relevance import baseline

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("a model was used")

    for name in ("build_model", "fit_and_choose_threshold", "run", "probabilities_of"):
        monkeypatch.setattr(baseline, name, forbidden)
    monkeypatch.setattr("sklearn.pipeline.Pipeline.fit", forbidden)
    monkeypatch.setattr("sklearn.pipeline.Pipeline.predict_proba", forbidden)
    articles, metadata, report = built(
        [row(1, "2026-09-18T00:00:00+00:00")], [old(2, "старый текст")]
    )
    write_frozen(tmp_path, articles, metadata, report)
    annotate(articles, tmp_path / "labels.jsonl", ask=answers("o"), say=lambda _: None)


# --- annotation ----------------------------------------------------------------------


def answers(*keys: str) -> Any:
    queue: Iterator[str] = iter(keys)

    def ask(prompt: str) -> str:
        return next(queue)

    return ask


def holdout(count: int) -> list[dict[str, Any]]:
    return [
        {**row(i, f"2026-09-{18 + i}T00:00:00+00:00"), "text_hash": "h"}
        for i in range(1, count + 1)
    ]


def test_labels_are_written_one_per_article(tmp_path: Path) -> None:
    path = tmp_path / "labels.jsonl"
    done = annotate(holdout(3), path, ask=answers("c", "y", "a", "c", "n"), say=lambda _: None)
    assert done == 3
    assert read_jsonl(path) == [
        {"article_id": 1, "kind": "case", "political": True, "annotation_source": "human"},
        {
            "article_id": 2,
            "kind": "administrative",
            "political": False,
            "annotation_source": "human",
        },
        {"article_id": 3, "kind": "case", "political": False, "annotation_source": "human"},
    ]


def test_political_is_asked_only_of_a_case(tmp_path: Path) -> None:
    prompts: list[str] = []
    keys = iter(["p", "f", "n", "o", "c", "n"])

    def ask(prompt: str) -> str:
        prompts.append(prompt)
        return next(keys)

    annotate(holdout(5), tmp_path / "labels.jsonl", ask=ask, say=lambda _: None)
    political = [p for p in prompts if p.startswith("political")]
    assert len(political) == 1
    assert prompts.index(political[0]) == 5


def test_every_kind_of_the_rubric_can_be_given(tmp_path: Path) -> None:
    path = tmp_path / "labels.jsonl"
    annotate(holdout(6), path, ask=answers("c", "n", "a", "p", "f", "n", "o"), say=lambda _: None)
    assert [label["kind"] for label in read_jsonl(path)] == list(KINDS)


def test_annotation_resumes_and_never_shows_a_labelled_article(tmp_path: Path) -> None:
    path = tmp_path / "labels.jsonl"
    articles = holdout(4)
    annotate(articles, path, ask=answers("o", "a", "q"), say=lambda _: None)
    assert [label["article_id"] for label in read_jsonl(path)] == [1, 2]
    shown: list[str] = []
    annotate(articles, path, ask=answers("p", "o"), say=shown.append)
    titles = [line for text in shown for line in text.splitlines() if line.startswith("title:")]
    assert titles == ["title:      Заголовок 3", "title:      Заголовок 4"]
    assert [label["article_id"] for label in read_jsonl(path)] == [1, 2, 3, 4]


def test_a_skip_is_not_a_label_and_comes_back(tmp_path: Path) -> None:
    path = tmp_path / "labels.jsonl"
    annotate(holdout(2), path, ask=answers("s", "o"), say=lambda _: None)
    assert read_jsonl(path) == [
        {"article_id": 2, "kind": "other", "political": False, "annotation_source": "human"}
    ]
    shown: list[str] = []
    annotate(holdout(2), path, ask=answers("q"), say=shown.append)
    assert any("title:      Заголовок 1" in text for text in shown)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["labels.jsonl"]


def test_a_wrong_key_is_asked_again_and_help_shows_the_rubric(tmp_path: Path) -> None:
    path = tmp_path / "labels.jsonl"
    shown: list[str] = []
    annotate(holdout(1), path, ask=answers("x", "?", "", "c", "maybe", "n"), say=shown.append)
    assert read_jsonl(path) == [
        {"article_id": 1, "kind": "case", "political": False, "annotation_source": "human"}
    ]
    assert any("СВЕЖУЮ процессуальную" in text for text in shown)


def test_the_screen_shows_no_model_output(tmp_path: Path) -> None:
    shown: list[str] = []
    articles = [{**holdout(1)[0], "text": "Суд " * 1000}]
    annotate(articles, tmp_path / "labels.jsonl", ask=answers("q"), say=shown.append, chars=40)
    screen = "\n".join(shown).lower()
    for word in ("probab", "threshold", "predict", "score", "вероятн"):
        assert word not in screen
    assert "Суд Суд" in "\n".join(shown)
    assert len(max(shown, key=len)) < 400


@pytest.mark.parametrize(
    ("line", "message"),
    [
        ({"article_id": 99, "kind": "case", "political": True}, "not in the holdout"),
        ({"article_id": 1, "kind": "skip", "political": False}, "unknown kind"),
        ({"article_id": 1, "kind": "other", "political": True}, "only a case"),
    ],
)
def test_labels_that_break_the_rubric_are_refused(
    tmp_path: Path, line: dict[str, Any], message: str
) -> None:
    path = tmp_path / "labels.jsonl"
    path.write_text(json.dumps(line) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_labels(path, holdout(2))


def test_a_political_that_is_not_true_or_false_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "labels.jsonl"
    path.write_text(json.dumps({"article_id": 1, "kind": "case", "political": "yes"}) + "\n")
    with pytest.raises(TypeError, match="true/false"):
        load_labels(path, holdout(2))


def test_the_kinds_are_those_of_junk_labels_v1() -> None:
    labels = pytest.importorskip("evaluation.junk_screen.labels")
    assert tuple(labels.KINDS) == KINDS
    assert labels.PROMPT_VERSION == "junk-labels-v1"
