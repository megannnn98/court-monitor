"""The holdout labelled anew under v2: only texts are copied, only texts are sent, and the
two annotators' agreement is kept apart from their disagreements."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from evaluation.relevance import holdout_v2
from evaluation.relevance.holdout import FrozenError
from evaluation.relevance.holdout_v2 import CONSENSUS, consensus, parse, prepare
from evaluation.relevance.person_criminal_case_rubric import PROMPT_VERSION
from evaluation.relevance.silver import payload

FIELD = "relevant_to_court_monitor"
WHEN = datetime(2026, 10, 15, tzinfo=UTC)


def sample_row(i: int) -> dict[str, Any]:
    return {
        "article_id": i,
        "title": f"t{i}",
        "text": f"текст {i}",
        "source": "s",
        "url": "u",
        "published_at": f"2026-09-{i:02d}T00:00:00+00:00",
        "text_hash": str(i),
        "old_label": True,
    }


def test_prepare_copies_texts_only_and_refuses_to_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from evaluation.relevance import sample_holdout

    holdout = tmp_path / "holdout"
    holdout.mkdir()
    rows = [sample_row(i) for i in (2, 1)]
    (holdout / "sample_articles.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    monkeypatch.setattr(sample_holdout, "check_frozen", lambda _: {"sample_sha256": "s"})
    out = tmp_path / "v2"
    metadata = prepare(holdout, out, created_at=WHEN)
    copied = [json.loads(line) for line in (out / "articles.jsonl").read_text().splitlines()]
    assert [r["article_id"] for r in copied] == [1, 2]
    assert all("old_label" not in r for r in copied)
    assert metadata["old_labels_used"] is False
    with pytest.raises(FrozenError):
        prepare(holdout, out, created_at=WHEN)


def test_the_request_holds_only_id_title_and_text() -> None:
    sent = json.loads(payload([sample_row(1)]))
    assert sent == [{"id": 1, "title": "t1", "text": "текст 1"}]


def test_an_answer_keeps_its_reason_and_the_rubric_version() -> None:
    content = json.dumps({"labels": [{"id": 7, FIELD: True, "reason": "поджог без жертв"}]})
    assert parse(content) == [
        (7, {FIELD: True, "reason": "поджог без жертв", "rubric_version": PROMPT_VERSION})
    ]


def test_an_answer_off_the_schema_is_refused() -> None:
    with pytest.raises(ValueError):
        parse(json.dumps({"labels": [{"id": 7, FIELD: True}]}))


def label(i: int, relevant: bool) -> dict[str, Any]:
    return {"article_id": i, FIELD: relevant, "reason": f"r{i}", "answered_by": "m"}


def test_agreement_and_disagreements_are_kept_apart() -> None:
    articles = [sample_row(i) for i in (1, 2, 3)]
    agreed, differing, report = consensus(
        articles,
        [label(1, True), label(2, False), label(3, True)],
        [label(1, True), label(2, False), label(3, False)],
    )
    assert [(r["article_id"], r[FIELD], r["annotation_source"]) for r in agreed] == [
        (1, True, CONSENSUS),
        (2, False, CONSENSUS),
    ]
    assert [(r["article_id"], r["label_a"], r["label_b"]) for r in differing] == [(3, True, False)]
    assert report["agree"] == 2
    assert report["a_true_b_false"] == 1


def test_an_annotator_missing_an_article_is_refused() -> None:
    articles = [sample_row(i) for i in (1, 2)]
    with pytest.raises(ValueError, match="annotator b"):
        consensus(articles, [label(1, True), label(2, True)], [label(1, True)])


def test_the_system_prompt_states_the_v2_special_rules() -> None:
    for words in ("дайджест", "только против организации", "релейного шкафа", "внешние знания"):
        assert words in holdout_v2.SYSTEM


def _endpoint(model: str = "cohere/command-a-plus") -> Any:
    from llm.chat import Endpoint

    return Endpoint(provider="openrouter", url="u", model=model, api_key="k")


def _disagreement(i: int) -> dict[str, Any]:
    return {"article_id": i, "label_a": True, "reason_a": "ra", "label_b": False, "reason_b": "rb"}


def _transport(answered_by: str = "cohere/command-a-plus", answer_id: int | None = None) -> Any:
    from evaluation.relevance.silver import Reply

    sent: list[str] = []

    def send(endpoint: Any, user: str) -> Reply:
        sent.append(user)
        asked = json.loads(user)[0]["id"]
        content = json.dumps(
            {"labels": [{"id": answer_id or asked, FIELD: False, "reason": "решение"}]}
        )
        return Reply(content=content, model=answered_by, cost_usd=0.001)

    send.sent = sent  # type: ignore[attr-defined]
    return send


def test_the_judge_sees_both_answers_and_nothing_else(tmp_path: Path) -> None:
    from evaluation.relevance.holdout_v2 import adjudicate

    transport = _transport()
    run = adjudicate(
        [sample_row(1)],
        [_disagreement(1)],
        tmp_path / "j.jsonl",
        endpoint=_endpoint(),
        transport=transport,
        say=lambda _: None,
    )
    sent = json.loads(transport.sent[0])
    assert sent == [
        {
            "id": 1,
            "title": "t1",
            "text": "текст 1",
            "annotator_a": {FIELD: True, "reason": "ra"},
            "annotator_b": {FIELD: False, "reason": "rb"},
        }
    ]
    assert run["error"] is None and run["decided"] == 1
    row = json.loads((tmp_path / "j.jsonl").read_text())
    assert row["answered_by"] == "cohere/command-a-plus"
    assert row["rubric_version"] == PROMPT_VERSION


def test_a_probe_asks_once(tmp_path: Path) -> None:
    from evaluation.relevance.holdout_v2 import adjudicate

    run = adjudicate(
        [sample_row(1), sample_row(2)],
        [_disagreement(1), _disagreement(2)],
        tmp_path / "j.jsonl",
        endpoint=_endpoint(),
        transport=_transport(),
        limit=1,
        say=lambda _: None,
    )
    assert run["asked"] == 1 and run["missing"] == 1


@pytest.mark.parametrize(
    ("transport", "message"),
    [
        (_transport(answered_by="other/model"), "answered by"),
        (_transport(answer_id=9), "answered for"),
    ],
)
def test_the_judge_stops_on_another_model_or_article(
    tmp_path: Path, transport: Any, message: str
) -> None:
    from evaluation.relevance.holdout_v2 import adjudicate

    run = adjudicate(
        [sample_row(1)],
        [_disagreement(1)],
        tmp_path / "j.jsonl",
        endpoint=_endpoint(),
        transport=transport,
        say=lambda _: None,
    )
    assert message in run["error"]
    assert not (tmp_path / "j.jsonl").exists()


def test_final_labels_take_consensus_and_adjudication_once_each() -> None:
    from evaluation.relevance.holdout_v2 import ADJUDICATION, final_labels

    articles = [sample_row(i) for i in (1, 2)]
    rows = final_labels(
        articles, [{"article_id": 1, FIELD: True}], [{"article_id": 2, FIELD: False}]
    )
    assert [(r["article_id"], r["annotation_source"]) for r in rows] == [
        (1, CONSENSUS),
        (2, ADJUDICATION),
    ]
    with pytest.raises(ValueError):
        final_labels(articles, [{"article_id": 1, FIELD: True}], [])


def test_the_audit_sample_is_seeded() -> None:
    from evaluation.relevance.holdout_v2 import audit_sample

    articles = [sample_row(i) for i in range(1, 29)] + [
        {**sample_row(1), "article_id": i} for i in range(29, 101)
    ]
    labels = [
        {"article_id": a["article_id"], FIELD: False, "annotation_source": CONSENSUS}
        for a in articles
    ]
    first, second = audit_sample(articles, labels), audit_sample(articles, labels)
    assert [r["article_id"] for r in first] == [r["article_id"] for r in second]
    assert len(first) == 50


def test_an_audit_correction_keeps_what_it_replaced() -> None:
    from evaluation.relevance.holdout_v2 import AUDIT_CORRECTION, corrected

    labels = [
        {"article_id": 1, FIELD: True, "annotation_source": CONSENSUS},
        {"article_id": 2, FIELD: False, "annotation_source": CONSENSUS},
    ]
    fix = {"article_id": 1, FIELD: False, "previous_label": True}
    rows = corrected(labels, [fix], [1, 2])
    assert rows[0] == {
        "article_id": 1,
        FIELD: False,
        "annotation_source": AUDIT_CORRECTION,
        "previous_label": True,
        "previous_annotation_source": CONSENSUS,
    }
    assert rows[1] == labels[1]
    with pytest.raises(ValueError, match="outside the audit"):
        corrected(labels, [fix], [2])
    with pytest.raises(ValueError, match="another label"):
        corrected(labels, [{**fix, "article_id": 2}], [1, 2])
