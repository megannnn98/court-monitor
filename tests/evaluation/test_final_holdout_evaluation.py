"""The final holdout evaluation: only the frozen pipeline, only audited frozen labels, and
the gate applied before the threshold."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sklearn")

from evaluation.relevance.baseline import Article
from evaluation.relevance.final_holdout_evaluation import checked, evaluate, holdout_articles

FIELD = "relevant_to_court_monitor"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_a_changed_gate_or_train_is_refused(tmp_path: Path) -> None:
    gate, train = tmp_path / "gate.py", tmp_path / "train.jsonl"
    gate.write_text("a")
    train.write_text("b")
    config = {"gate_source_sha256": _sha(gate), "train_sha256": _sha(train)}
    checked(config, gate, train)
    gate.write_text("changed")
    with pytest.raises(ValueError, match="gate changed"):
        checked(config, gate, train)


def _holdout(tmp_path: Path, audited: bool = True) -> Path:
    article = {
        "article_id": 1,
        "title": "t",
        "text": "x",
        "source": "s",
        "published_at": "2026-09-20T00:00:00+00:00",
    }
    (tmp_path / "articles.jsonl").write_text(json.dumps(article) + "\n")
    labels = tmp_path / "final_labels_v2.jsonl"
    labels.write_text(json.dumps({"article_id": 1, FIELD: True, "annotation_source": "c"}) + "\n")
    metadata: dict[str, Any] = {"frozen": True, "final_labels_sha256": _sha(labels)}
    if audited:
        metadata["audit_corrections"] = 0
    (tmp_path / "final_dataset_metadata_v2.json").write_text(json.dumps(metadata))
    return tmp_path


def test_only_audited_frozen_labels_are_judged(tmp_path: Path) -> None:
    (tmp_path / "audited").mkdir()
    (tmp_path / "raw").mkdir()
    assert [a.label for a in holdout_articles(_holdout(tmp_path / "audited"))] == [1]
    with pytest.raises(ValueError, match="not audited"):
        holdout_articles(_holdout(tmp_path / "raw", audited=False))


def article(i: int, text: str, label: int) -> Article:
    return Article(i, "t", text, datetime(2026, 9, 1, tzinfo=UTC), label, "c", "s", "")


def test_the_gate_is_applied_before_the_threshold() -> None:
    class Model:
        def fit(self, *_: Any) -> None:
            return None

        def predict_proba(self, texts: list[str]) -> Any:
            import numpy

            return numpy.array([[0.1, 0.9] for _ in texts])

    holdout = [
        article(1, "Суд оштрафовал жительницу по КоАП.", 0),
        article(2, "Активиста приговорили по уголовному делу.", 1),
        article(3, "Мужчину приговорили по уголовному делу.", 0),
    ]
    report = evaluate(holdout, holdout, 0.5, str, Model)
    assert report["gate_dropped"] == 1
    assert report["metrics"]["fp"] == 1
    assert report["classifier_without_gate"]["fp"] == 2
    assert report["gate_relevant_dropped"] == []
    assert [e["article_id"] for e in report["false_positives_top20"]] == [3]
