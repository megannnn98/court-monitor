"""Independent scoring examples and reproducible replay of supplied manual labels."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from evaluation.identity_comparison import evaluate_manual as evaluation
from evaluation.identity_comparison import labelling

FIXTURES = Path(__file__).parents[1] / "fixtures" / "identity_comparison"


def dataset(decisions: list[tuple[str, str]]) -> dict[str, Any]:
    return {
        "labels": list(labelling.LABELS[:3]),
        "cases": [
            {
                "case_id": f"invented-{index}",
                "stratum": "rules_join_er_splits/invented",
                "decisions": {"er_v2": er, "rules": rules},
                "sides": [{"side": "a", "mentions": []}, {"side": "b", "mentions": []}],
                "pairs": [
                    {
                        "pair_id": f"invented-{index}:a-b",
                        "sides": ["a", "b"],
                        "annotation": {"label": None, "comment": None},
                    }
                ],
            }
            for index, (er, rules) in enumerate(decisions, start=1)
        ],
    }


def document(labels: list[str | None], version: int = 2) -> dict[str, Any]:
    return {
        "version": version,
        "annotations": {
            f"invented-{index}:a-b": {"label": label, "skipped": label is None}
            for index, label in enumerate(labels, start=1)
        },
    }


@pytest.mark.parametrize("version", [1, 2])
def test_load_versions_preserves_original(version: int, tmp_path: Path) -> None:
    source = dataset([("one_person", "one_entity")])
    data = document(["same_person"], version)
    path = tmp_path / "labels.json"
    path.write_text(json.dumps(data))
    original = path.read_bytes()
    assert labelling.load_annotations(path, labelling.pairs_of(source)) == data
    assert path.read_bytes() == original


@pytest.mark.parametrize(("key", "label"), list(labelling.ANSWERS.items()))
def test_all_six_keyboard_labels_roundtrip(key: str, label: str, tmp_path: Path) -> None:
    pairs = labelling.pairs_of(dataset([("one_person", "one_entity")]))
    path = tmp_path / "labels.json"
    data = document([None], 1)
    labelling.run(pairs, data, path, read=lambda _: key, write=lambda _: None)
    loaded = labelling.load_annotations(path, pairs)
    assert loaded["annotations"][pairs[0].identifier]["label"] == label
    assert loaded["version"] == (2 if label in labelling.LABELS[3:] else 1)
    stats = labelling.statistics(pairs, loaded)
    assert stats[label] == 1 and stats["labelled"] == 1 and stats["unlabelled"] == 0
    shown: list[str] = []
    labelling.run(
        pairs, loaded, path, read=lambda _: pytest.fail("already labelled"), write=shown.append
    )
    assert not any("pair 1/1" in line for line in shown)


def test_edit_preserves_metadata_and_merge_flags(tmp_path: Path) -> None:
    sample = dataset([("one_person", "one_entity")])
    pairs = labelling.pairs_of(sample)
    identifier = pairs[0].identifier
    data = document(["same_person"])
    data["annotations"][identifier].update(index=1, flags=["bad_span"], comment="Invented note")
    path = tmp_path / "labels.json"
    labelling.run(pairs, data, path, edit=identifier, read=lambda _: "g", write=lambda _: None)
    entry = labelling.load_annotations(path, pairs)["annotations"][identifier]
    assert entry == {
        "index": 1,
        "flags": ["bad_span"],
        "comment": "Invented note",
        "label": "mixed_identity_group",
        "skipped": False,
    }
    merged = labelling.merged_sample(sample, data)
    assert merged["cases"][0]["pairs"][0]["annotation"] == {
        "label": "mixed_identity_group",
        "comment": "Invented note",
        "flags": ["bad_span"],
    }
    assert merged["labels"] == list(labelling.LABELS)
    assert sample["cases"][0]["pairs"][0]["annotation"]["label"] is None
    labelling.record(
        path, data, identifier, "different_person", overwrite=True, flags=[], comment="Changed note"
    )
    assert data["annotations"][identifier]["flags"] == []
    assert data["annotations"][identifier]["comment"] == "Changed note"
    assert data["annotations"][identifier]["index"] == 1


@pytest.mark.parametrize("flags", [["made_up"], ["bad_span", "bad_span"], "bad_span"])
def test_invalid_flags_refused(flags: Any, tmp_path: Path) -> None:
    data = document(["same_person"])
    data["annotations"]["invented-1:a-b"]["flags"] = flags
    path = tmp_path / "labels.json"
    path.write_text(json.dumps(data))
    with pytest.raises((ValueError, TypeError)):
        labelling.load_annotations(
            path, labelling.pairs_of(dataset([("one_person", "one_entity")]))
        )


def test_identity_metrics_abstention_and_error_direction() -> None:
    sample = dataset(
        [
            ("different_persons", "one_entity"),  # ER split, rules right
            ("one_person", "different_entities"),  # ER merge, rules right
            ("REVIEW", "one_entity"),  # abstention, not an error
            ("different_persons", "one_entity"),  # ER right, rules merge
            ("one_person", "different_entities"),  # ER right, rules split
        ]
    )
    report = evaluation.evaluate(
        sample,
        document(
            ["same_person", "different_person", "same_person", "different_person", "same_person"]
        ),
    )
    er, rules = report["models"]["er_v2"], report["models"]["rules"]
    assert (er["automatic_decisions"], er["correct"], er["abstains"]) == (4, 2, 1)
    assert er["automatic_accuracy"] == 0.5 and er["automatic_coverage"] == 0.8
    assert er["correct_fraction_of_evaluable"] == 0.4
    assert (er["false_merges"], er["false_splits"]) == (1, 1)
    assert (rules["automatic_decisions"], rules["correct"], rules["abstains"]) == (5, 3, 0)
    assert rules["automatic_accuracy"] == 0.6 and rules["automatic_coverage"] == 1.0
    assert (rules["false_merges"], rules["false_splits"]) == (1, 1)
    assert report["errors"]["er_v2_abstains"][0]["pair_id"] == "invented-3:a-b"
    assert report["errors"]["rules_false_merges"][0]["pair_id"] == "invented-4:a-b"
    assert report["by_stratum"]["rules_join_er_splits"]["evaluable"] == 5


def test_special_labels_and_unlabelled_excluded_with_zero_denominators() -> None:
    labels = [*labelling.LABELS[2:], None]
    report = evaluation.evaluate(
        dataset([("one_person", "different_entities")] * len(labels)), document(labels)
    )
    assert (
        report["overall"]["total_pairs"] == 5 and report["overall"]["evaluable_identity_pairs"] == 0
    )
    for label in labelling.LABELS[2:]:
        assert report["overall"][label] == 1
    for model in report["models"].values():
        assert model["automatic_decisions"] == model["wrong"] == model["abstains"] == 0
        assert model["automatic_accuracy"] is None and model["automatic_coverage"] is None
    assert len(report["errors"]["special_or_unlabelled"]) == 5
    assert report["models"]["er_v2"]["all_pairs_automatic"] == 5
    assert len(report["error_categories"]["mixed_or_invalid_cases"]) == 2


def test_overlapping_flags_are_not_exclusions_or_exclusive_categories() -> None:
    data = document(["same_person"])
    data["annotations"]["invented-1:a-b"]["flags"] = [
        "bad_span",
        "bad_normalization",
        "ner_error",
        "name_order",
        "initial_expansion",
    ]
    report = evaluation.evaluate(dataset([("different_persons", "one_entity")]), data)
    assert all(report["diagnostics"][flag] == 1 for flag in labelling.FLAGS)
    assert report["models"]["er_v2"]["false_splits"] == 1
    assert (
        report["error_categories"]["identity_errors_without_extraction_or_normalization_flags"]
        == []
    )
    assert report["error_categories"]["identity_errors_with_extraction_flags"] == ["invented-1:a-b"]
    assert report["error_categories"]["identity_errors_with_normalization_flags"] == [
        "invented-1:a-b"
    ]


def test_unknown_decision_or_index_mismatch_is_not_silently_scored() -> None:
    with pytest.raises(ValueError, match="unknown"):
        evaluation.evaluate(dataset([("mystery", "one_entity")]), document(["same_person"]))
    data = document(["same_person"])
    data["annotations"]["invented-1:a-b"]["index"] = 2
    with pytest.raises(ValueError, match="index"):
        evaluation.evaluate(dataset([("one_person", "one_entity")]), data)


def test_supplied_benchmark_and_provenance() -> None:
    manifest = json.loads((FIXTURES / "provenance.json").read_text())
    for name, metadata in manifest["files"].items():
        assert hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest() == metadata["sha256"]
    sample = json.loads((FIXTURES / "disagreement_decisions.json").read_text())
    pairs = labelling.pairs_of(sample)
    legacy = labelling.load_annotations(FIXTURES / "manual_annotations.json", pairs)
    assert labelling.statistics(pairs, legacy)["labelled"] == 94
    assert labelling.statistics(pairs, legacy)["skipped"] == 6
    data = labelling.load_annotations(FIXTURES / "manual_annotations_extended.json", pairs)
    assert len(data["annotations"]) == len(pairs) == 100
    assert labelling.statistics(pairs, data)["labelled"] == 100
    report = evaluation.evaluate(sample, data)
    preliminary = json.loads((FIXTURES / "identity_disagreement_results.json").read_text())
    assert evaluation.compare_reference(report, preliminary) == []
    # Two manually verified namesakes: stored rules explicitly join each pair.
    for identifier in ("case-745d2982ff94:a-b", "case-8501beb5abd5:a-b"):
        row = next(
            row for row in report["errors"]["rules_false_merges"] if row["pair_id"] == identifier
        )
        assert row["manual_label"] == "different_person"
        assert row["rules_decision"] == "one_entity"
        assert row["er_v2_outcome"] == "correct"
    changed = json.loads(json.dumps(preliminary))
    changed["rules_false_merge_pairs"].pop()
    assert evaluation.compare_reference(report, changed)[0]["actual_only"] == [
        "case-8501beb5abd5:a-b"
    ]


def test_evaluator_cli_does_not_import_database_or_api_clients(tmp_path: Path) -> None:
    script = """
import builtins, runpy, socket, sys
sys.path.insert(0, sys.argv.pop(1))
original = builtins.__import__
def checked(name, *args, **kwargs):
    if name.split('.')[0] in {'db','sqlalchemy','psycopg','httpx','requests'}:
        raise AssertionError('database or API imported')
    return original(name, *args, **kwargs)
def no_network(*args, **kwargs):
    raise AssertionError('network attempted')
builtins.__import__ = checked
socket.socket.connect = no_network
runpy.run_module('evaluation.identity_comparison.evaluate_manual', run_name='__main__')
"""
    output = tmp_path / "report.json"
    command = [
        sys.executable,
        "-I",
        "-c",
        script,
        str(Path(labelling.__file__).parents[2]),
        "--sample",
        str(FIXTURES / "disagreement_decisions.json"),
        "--annotations",
        str(FIXTURES / "manual_annotations_extended.json"),
        "--out",
        str(output),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)["overall"]["evaluable_identity_pairs"] == 93
    report = json.loads(output.read_text())
    row = report["errors"]["rules_false_merges"][0]
    assert {
        "index",
        "pair_id",
        "case_id",
        "stratum",
        "manual_label",
        "er_v2_decision",
        "rules_decision",
        "flags",
        "comment",
    } <= set(row)
    assert "article" not in row and "context" not in row
