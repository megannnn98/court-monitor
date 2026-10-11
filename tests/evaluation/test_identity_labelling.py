"""Local labelling with invented names and text; no real corpus is used."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from evaluation.identity_comparison import labelling


@pytest.fixture
def sample() -> dict[str, Any]:
    sides = [
        {
            "side": letter,
            "mentions": [
                {
                    "surface": "Ивана Сидорова",
                    "normalized": "Иван Сидоров",
                    "article": {
                        "title": f"Вымышленная статья {index}",
                        "published_at": "2026-01-02",
                        "source": "Вестник Энска",
                    },
                    "context": {"text": f"Вымышленный контекст {index}"},
                    "er_decision": {"action": "hidden_decision"},
                }
                for index in range(2)
            ],
        }
        for letter in "abc"
    ]
    return {
        "cases": [
            {
                "case_id": "case-invented",
                "sides": sides,
                "decisions": {"er_v2": "different_persons", "rules": "one_entity"},
                "pairs": [
                    {
                        "pair_id": f"case-invented:{left}-{right}",
                        "sides": [left, right],
                        "annotation": {"label": None, "comment": None},
                    }
                    for left, right in (("a", "b"), ("a", "c"), ("b", "c"))
                ],
            },
            {"case_id": "case-alone", "pairs": [], "sides": [], "not_ready": "alone"},
        ]
    }


def empty() -> dict[str, Any]:
    return {"version": 1, "annotations": {}}


def test_save_after_each_answer_and_resume_first_unlabelled(
    sample: dict[str, Any], tmp_path: Path
) -> None:
    path = tmp_path / "answers.json"
    pairs = labelling.pairs_of(sample)
    document = empty()
    prompts = 0

    def answer(_prompt: str) -> str:
        nonlocal prompts
        prompts += 1
        if prompts == 1:
            return "s"
        assert (
            json.loads(path.read_text())["annotations"][pairs[0].identifier]["label"]
            == "same_person"
        )
        raise KeyboardInterrupt

    labelling.run(pairs, document, path, read=answer, write=lambda _: None)
    reloaded = labelling.load_annotations(path, pairs)
    shown: list[str] = []
    labelling.run(pairs, reloaded, path, read=lambda _: "q", write=shown.append)
    assert "pair 2/3" in shown[0]
    assert pairs[0].identifier not in "\n".join(shown)
    assert reloaded == document


def test_skip_returns_on_resume_and_multiple_pairs_have_distinct_answers(
    sample: dict[str, Any], tmp_path: Path
) -> None:
    pairs = labelling.pairs_of(sample)
    document = empty()
    answers = iter(("k", "d", "i"))
    labelling.run(
        pairs,
        document,
        tmp_path / "answers.json",
        read=lambda _: next(answers),
        write=lambda _: None,
    )
    assert labelling.statistics(pairs, document) == {
        "total": 3,
        "labelled": 2,
        "same_person": 0,
        "different_person": 1,
        "insufficient_evidence": 1,
        "not_a_person": 0,
        "mixed_identity_group": 0,
        "invalid_mention": 0,
        "skipped": 1,
        "unlabelled": 1,
    }
    shown: list[str] = []
    labelling.run(
        pairs, document, tmp_path / "answers.json", read=lambda _: "q", write=shown.append
    )
    assert "pair 1/3" in shown[0]


def test_overwrite_requires_explicit_edit(sample: dict[str, Any], tmp_path: Path) -> None:
    pairs = labelling.pairs_of(sample)
    path = tmp_path / "answers.json"
    document = empty()
    identifier = pairs[0].identifier
    labelling.record(path, document, identifier, "same_person")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="explicit --edit"):
        labelling.record(path, document, identifier, "different_person")
    assert path.read_bytes() == before
    labelling.run(pairs, document, path, edit=identifier, read=lambda _: "d", write=lambda _: None)
    assert (
        labelling.load_annotations(path, pairs)["annotations"][identifier]["label"]
        == "different_person"
    )
    labelling.run(pairs, document, path, edit=identifier, read=lambda _: "k", write=lambda _: None)
    assert document["annotations"][identifier]["label"] == "different_person"


def test_invalid_label_does_not_write_and_invalid_key_reprompts(
    sample: dict[str, Any], tmp_path: Path
) -> None:
    path = tmp_path / "answers.json"
    pairs = labelling.pairs_of(sample)
    document = empty()
    with pytest.raises(ValueError, match="invalid label"):
        labelling.record(path, document, pairs[0].identifier, "made_up")
    assert not path.exists() and document == empty()
    answers = iter(("z", "s", "q"))
    shown: list[str] = []
    labelling.run(pairs, document, path, read=lambda _: next(answers), write=shown.append)
    assert any("Invalid answer" in line for line in shown)
    assert labelling.statistics(pairs, document)["labelled"] == 1


def test_context_precedes_models_and_all_mentions_are_shown(
    sample: dict[str, Any], tmp_path: Path
) -> None:
    shown: list[str] = []
    answers = iter(("m", "q"))
    labelling.run(
        labelling.pairs_of(sample),
        empty(),
        tmp_path / "answers.json",
        read=lambda _: next(answers),
        write=shown.append,
    )
    model_index = next(index for index, line in enumerate(shown) if "er_v2" in line)
    assert all(
        "different_persons" not in line and "one_entity" not in line for line in shown[:model_index]
    )
    assert sum("Вымышленный контекст" in line for line in shown[:model_index]) == 4
    assert "[A]" in "\n".join(shown) and "[B]" in "\n".join(shown)
    assert "Вестник Энска" in "\n".join(shown)
    assert "hidden_decision" not in "\n".join(shown)


def test_atomic_save_flushes_before_replace_and_preserves_old_file_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "answers.json"
    labelling.atomic_write(path, empty())
    original = path.read_bytes()
    fsync = os.fsync
    events: list[str] = []

    def sync(fd: int) -> None:
        events.append("fsync")
        fsync(fd)

    def fail_replace(source: Path, destination: Path) -> None:
        assert source.parent == destination.parent
        assert destination.read_bytes() == original
        assert json.loads(source.read_text()) == {"new": "complete"}
        assert events == ["fsync"]
        raise OSError("simulated replacement failure")

    monkeypatch.setattr(os, "fsync", sync)
    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="replacement failure"):
        labelling.atomic_write(path, {"new": "complete"})
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


def test_failed_save_does_not_change_in_memory_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = empty()

    def fail(_path: Path, _document: dict[str, Any]) -> None:
        raise OSError("disk failure")

    monkeypatch.setattr(labelling, "atomic_write", fail)
    with pytest.raises(OSError, match="disk failure"):
        labelling.record(tmp_path / "answers.json", document, "invented", "same_person")
    assert document == empty()


def test_successful_save_syncs_file_and_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fsync = os.fsync
    synced: list[int] = []

    def sync(fd: int) -> None:
        synced.append(fd)
        fsync(fd)

    monkeypatch.setattr(os, "fsync", sync)
    path = tmp_path / "answers.json"
    labelling.atomic_write(path, {"complete": True})
    assert json.loads(path.read_text()) == {"complete": True}
    assert len(synced) == 2
    assert list(tmp_path.iterdir()) == [path]


def test_merge_preserves_source_and_ids(sample: dict[str, Any], tmp_path: Path) -> None:
    original = copy.deepcopy(sample)
    pairs = labelling.pairs_of(sample)
    document = empty()
    labelling.record(tmp_path / "answers.json", document, pairs[1].identifier, "different_person")
    merged = labelling.merged_sample(sample, document)
    assert sample == original
    assert [pair.identifier for pair in labelling.pairs_of(merged)] == [
        pair.identifier for pair in pairs
    ]
    assert merged["cases"][0]["pairs"][1]["annotation"]["label"] == "different_person"
    assert merged["cases"][0]["pairs"][0]["annotation"]["label"] is None
    assert merged["cases"][1] == original["cases"][1]


@pytest.mark.parametrize("entry", [{"label": "bad"}, {"label": "same_person", "skipped": True}])
def test_invalid_stored_annotations_are_refused(
    sample: dict[str, Any], tmp_path: Path, entry: dict[str, Any]
) -> None:
    path = tmp_path / "answers.json"
    path.write_text(json.dumps({"version": 1, "annotations": {"case-invented:a-b": entry}}))
    with pytest.raises(ValueError):
        labelling.load_annotations(path, labelling.pairs_of(sample))


def test_unknown_pair_and_duplicate_ids_are_refused(sample: dict[str, Any], tmp_path: Path) -> None:
    path = tmp_path / "answers.json"
    path.write_text(json.dumps({"version": 1, "annotations": {"unknown": {"label": None}}}))
    with pytest.raises(ValueError, match="absent"):
        labelling.load_annotations(path, labelling.pairs_of(sample))
    sample["cases"][0]["pairs"].append(sample["cases"][0]["pairs"][0])
    with pytest.raises(ValueError, match="duplicate"):
        labelling.pairs_of(sample)


def test_cli_has_no_database_or_network_dependencies(
    sample: dict[str, Any], tmp_path: Path
) -> None:
    source = tmp_path / "sample.json"
    source.write_text(json.dumps(sample))
    original = source.read_bytes()
    annotations = tmp_path / "answers.json"
    script = """
import builtins, runpy, socket, sys
sys.path.insert(0, sys.argv.pop(1))
original_import = builtins.__import__
def checked_import(name, *args, **kwargs):
    if name.split('.')[0] in {'db', 'sqlalchemy', 'psycopg', 'httpx', 'requests'}:
        raise AssertionError('database or API dependency imported')
    return original_import(name, *args, **kwargs)
def no_network(*args, **kwargs):
    raise AssertionError('network connection attempted')
builtins.__import__ = checked_import
socket.socket.connect = no_network
socket.create_connection = no_network
runpy.run_module('evaluation.identity_comparison.labelling', run_name='__main__')
"""
    base = [
        sys.executable,
        "-I",
        "-c",
        script,
        str(Path(labelling.__file__).parents[2]),
        "--sample",
        str(source),
        "--annotations",
        str(annotations),
    ]
    result = subprocess.run(base, input="s\nq\n", text=True, capture_output=True, check=True)
    assert "Saved." in result.stdout
    result = subprocess.run([*base, "--stats"], text=True, capture_output=True, check=True)
    assert json.loads(result.stdout)["labelled"] == 1
    merged = tmp_path / "merged.json"
    subprocess.run([*base, "--merge-out", str(merged)], capture_output=True, check=True)
    assert (
        json.loads(merged.read_text())["cases"][0]["pairs"][0]["annotation"]["label"]
        == "same_person"
    )
    rejected = subprocess.run([*base, "--merge-out", str(source)], capture_output=True, check=False)
    assert rejected.returncode == 2
    assert source.read_bytes() == original
