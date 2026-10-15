"""The fixed random sample of the relevance holdout and its labelling: drawn from the list
of articles alone, frozen once written, labelled apart from the holdout, a hand label
copied only to the very same normalized text."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from evaluation.relevance.annotate_holdout import annotate, load_labels, main
from evaluation.relevance.holdout import FrozenError, read_jsonl, text_hash
from evaluation.relevance.sample_holdout import (
    SAMPLE_SIZE,
    SEED,
    check_frozen,
    draw,
    make_sample,
    sha256_of,
)

CREATED = datetime(2026, 10, 15, 12, tzinfo=UTC)


def article(article_id: int, text: str = "") -> dict[str, Any]:
    body = text or f"Текст статьи {article_id}"
    return {
        "article_id": article_id,
        "title": f"Заголовок {article_id}",
        "text": body,
        "source": "Медиазона",
        "url": f"https://example.org/{article_id}",
        "published_at": f"2026-09-{20 + article_id % 10:02d}T{article_id % 24:02d}:00:00Z",
        "text_hash": text_hash(body),
    }


def holdout_dir(tmp_path: Path, size: int = 1632, first_id: int = 21003) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "articles.jsonl").write_text(
        "".join(json.dumps(article(first_id + i), ensure_ascii=False) + "\n" for i in range(size)),
        encoding="utf-8",
    )
    (tmp_path / "metadata.json").write_text(
        json.dumps({"dataset": "relevance-holdout-v1", "frozen": True}), encoding="utf-8"
    )
    return tmp_path


# --- the draw ------------------------------------------------------------------------


def test_the_sample_is_400_distinct_articles_of_the_holdout(tmp_path: Path) -> None:
    directory = holdout_dir(tmp_path)
    metadata = make_sample(directory, created_at=CREATED)
    sample = read_jsonl(directory / "sample_articles.jsonl")
    population = {a["article_id"] for a in read_jsonl(directory / "articles.jsonl")}
    ids = [a["article_id"] for a in sample]
    assert len(ids) == SAMPLE_SIZE == 400
    assert len(set(ids)) == 400
    assert set(ids) <= population
    assert metadata["article_ids"] == sorted(ids)


def test_the_same_seed_draws_the_same_articles() -> None:
    population = list(range(21003, 21003 + 1632))
    assert draw(population) == draw(list(reversed(population)))
    assert draw(population) == draw(population, 400, SEED)
    assert draw(population, seed=SEED + 1) != draw(population)


def test_the_draw_sees_only_the_list_of_ids(tmp_path: Path) -> None:
    plain = holdout_dir(tmp_path / "plain")
    labelled = holdout_dir(tmp_path / "labelled")
    # Labels, predictions and a stratum beside the holdout change nothing.
    rows = read_jsonl(labelled / "articles.jsonl")
    (labelled / "articles.jsonl").write_text(
        "".join(
            json.dumps(
                {**row, "kind": "case", "probability": 0.99, "stratum": "kept"},
                ensure_ascii=False,
            )
            + "\n"
            for row in reversed(rows)
        ),
        encoding="utf-8",
    )
    (labelled / "labels.jsonl").write_text(
        json.dumps({"article_id": rows[0]["article_id"], "kind": "case", "political": True}),
        encoding="utf-8",
    )
    first = make_sample(plain, created_at=CREATED)
    second = make_sample(labelled, created_at=CREATED)
    assert first["article_ids"] == second["article_ids"]


@pytest.mark.parametrize(
    ("ids", "size", "message"),
    [
        ([1, 2, 2], 2, "repeats"),
        ([1, 2, 3], 4, "cannot draw 4 of 3"),
        ([1, 2, 3], 0, "cannot draw 0"),
    ],
)
def test_an_impossible_draw_is_refused(ids: list[int], size: int, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        draw(ids, size)


# --- freezing ------------------------------------------------------------------------


def test_the_holdout_itself_is_not_changed(tmp_path: Path) -> None:
    directory = holdout_dir(tmp_path)
    before = sha256_of(directory / "articles.jsonl")
    metadata = make_sample(directory, created_at=CREATED)
    assert sha256_of(directory / "articles.jsonl") == before == metadata["population_sha256"]
    assert metadata["sample_sha256"] == sha256_of(directory / "sample_articles.jsonl")


def test_the_metadata_says_how_the_sample_was_made(tmp_path: Path) -> None:
    directory = holdout_dir(tmp_path)
    metadata = make_sample(directory, created_at=CREATED)
    assert metadata == metadata | {
        "dataset": "relevance-holdout-v1-sample",
        "population_size": 1632,
        "sample_size": 400,
        "sampling": "uniform_without_replacement",
        "seed": 20261015,
        "predictions_used": False,
        "labels_used": False,
        "frozen": True,
        "created_at": "2026-10-15T12:00:00Z",
        "unique_normalized_text_hashes": 400,
    }
    on_disk = json.loads((directory / "sample_metadata.json").read_text(encoding="utf-8"))
    assert on_disk == metadata


def test_a_frozen_sample_is_never_redrawn(tmp_path: Path) -> None:
    directory = holdout_dir(tmp_path)
    make_sample(directory, created_at=CREATED)
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    with pytest.raises(FrozenError, match="frozen"):
        make_sample(directory, seed=1, created_at=CREATED)
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == before


def test_a_sample_of_an_unfrozen_holdout_is_refused(tmp_path: Path) -> None:
    directory = holdout_dir(tmp_path)
    (directory / "metadata.json").write_text(json.dumps({"frozen": False}), encoding="utf-8")
    with pytest.raises(ValueError, match="not frozen"):
        make_sample(directory, created_at=CREATED)


def test_a_changed_sample_is_caught(tmp_path: Path) -> None:
    directory = holdout_dir(tmp_path)
    make_sample(directory, created_at=CREATED)
    check_frozen(directory)
    path = directory / "sample_articles.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="sample_articles.jsonl changed"):
        check_frozen(directory)


# --- labelling the sample ------------------------------------------------------------


def answers(*keys: str) -> Any:
    queue: Iterator[str] = iter(keys)
    return lambda prompt: next(queue)


def test_the_sample_is_labelled_apart_from_the_holdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = holdout_dir(tmp_path, size=30)
    make_sample(directory, size=5, created_at=CREATED)
    (directory / "labels.jsonl").write_text("", encoding="utf-8")
    before = (directory / "labels.jsonl").read_bytes()
    keys = iter(["o", "a", "q"])
    shown: list[str] = []
    monkeypatch.setattr("builtins.input", lambda prompt: next(keys))
    monkeypatch.setattr("builtins.print", lambda *a, **k: shown.append(" ".join(map(str, a))))
    assert main(["--dir", str(directory), "--sample"]) == 0
    assert (directory / "labels.jsonl").read_bytes() == before
    labels = read_jsonl(directory / "sample_labels.jsonl")
    sample_ids = [a["article_id"] for a in read_jsonl(directory / "sample_articles.jsonl")]
    assert [label["article_id"] for label in labels] == sample_ids[:2]
    assert any("/ 5" in line for line in shown)


def test_labelling_refuses_a_sample_changed_after_freezing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = holdout_dir(tmp_path, size=30)
    make_sample(directory, size=5, created_at=CREATED)
    path = directory / "sample_articles.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        main(["--dir", str(directory), "--sample"])
    assert not (directory / "sample_labels.jsonl").exists()


def twins() -> list[dict[str, Any]]:
    return [
        article(1, "Суд арестовал активиста"),
        article(2, "суд   АРЕСТОВАЛ активиста"),  # the same normalized text
        article(3, "Суд арестовал активиста."),  # one character more: another text
        article(4, "Совсем другой текст"),
    ]


def test_a_hand_label_goes_to_the_same_normalized_text_only(tmp_path: Path) -> None:
    path = tmp_path / "sample_labels.jsonl"
    articles = twins()
    shown: list[str] = []
    done = annotate(
        articles, path, ask=answers("c", "y", "a", "o"), say=shown.append, propagate=True
    )
    assert done == 3
    labels = {label["article_id"]: label for label in read_jsonl(path)}
    assert labels[1] == {
        "article_id": 1,
        "kind": "case",
        "political": True,
        "annotation_source": "human",
    }
    assert labels[2] == {
        "article_id": 2,
        "kind": "case",
        "political": True,
        "annotation_source": "duplicate_propagation",
        "propagated_from_article_id": 1,
    }
    assert labels[3]["annotation_source"] == "human"
    assert labels[3]["kind"] == "administrative"
    titles = [line for text in shown for line in text.splitlines() if line.startswith("title:")]
    assert "title:      Заголовок 2" not in titles


def test_without_propagation_every_twin_is_asked(tmp_path: Path) -> None:
    path = tmp_path / "labels.jsonl"
    annotate(twins(), path, ask=answers("o", "o", "o", "o"), say=lambda _: None)
    assert {label["annotation_source"] for label in read_jsonl(path)} == {"human"}


def test_a_skip_is_never_propagated(tmp_path: Path) -> None:
    path = tmp_path / "sample_labels.jsonl"
    annotate(twins(), path, ask=answers("s", "q"), say=lambda _: None, propagate=True)
    assert not path.exists()


def write(path: Path, *labels: dict[str, Any]) -> None:
    path.write_text("".join(json.dumps(label) + "\n" for label in labels), encoding="utf-8")


def human(article_id: int, kind: str = "case", political: bool = True) -> dict[str, Any]:
    return {
        "article_id": article_id,
        "kind": kind,
        "political": political,
        "annotation_source": "human",
    }


def copied(
    article_id: int, origin: Any, kind: str = "case", political: bool = True
) -> dict[str, Any]:
    return {
        "article_id": article_id,
        "kind": kind,
        "political": political,
        "annotation_source": "duplicate_propagation",
        "propagated_from_article_id": origin,
    }


def test_a_correct_propagation_loads(tmp_path: Path) -> None:
    path = tmp_path / "sample_labels.jsonl"
    write(path, human(1), copied(2, 1))
    assert load_labels(path, twins())[2]["propagated_from_article_id"] == 1


@pytest.mark.parametrize(
    ("labels", "message"),
    [
        ((human(1), copied(2, 99)), "not in the sample"),
        ((copied(2, 1),), "not labelled by hand"),
        ((human(1), copied(2, 1), copied(4, 2)), "not labelled by hand"),
        ((human(1), copied(3, 1)), "another text"),
        ((human(1), copied(2, 1, political=False)), "differs"),
        ((human(1), copied(2, 1, kind="other", political=False)), "differs"),
        (({**human(1), "propagated_from_article_id": 2},), "a human label propagated"),
        (({**human(1), "annotation_source": "model"},), "unknown annotation_source"),
        ((human(1), human(1)), "labelled twice"),
    ],
)
def test_a_propagation_conflict_is_an_error(
    tmp_path: Path, labels: tuple[dict[str, Any], ...], message: str
) -> None:
    path = tmp_path / "sample_labels.jsonl"
    write(path, *labels)
    before = path.read_bytes()
    with pytest.raises(ValueError, match=message):
        load_labels(path, twins())
    assert path.read_bytes() == before


def test_a_human_label_with_null_origin_loads(tmp_path: Path) -> None:
    path = tmp_path / "sample_labels.jsonl"
    write(path, {**human(4, "other", False), "propagated_from_article_id": None})
    assert load_labels(path, twins())[4]["annotation_source"] == "human"
