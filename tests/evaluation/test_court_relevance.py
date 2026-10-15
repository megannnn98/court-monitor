"""`court-monitor-relevance-v1`: two LLM annotators of a new target, consensus on the one
boolean, a person where they differ, an audit — and nothing taken from `junk-labels-v1`.
No network: the provider is faked."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from evaluation.relevance.annotate_court_monitor_relevance import adjudicate, audit
from evaluation.relevance.court_relevance import TASK, parse
from evaluation.relevance.court_relevance_consensus import (
    audit_agreement,
    build,
    by_article,
    disagreement,
    freeze_audit,
    rebuild,
)
from evaluation.relevance.court_relevance_rubric import PROMPT_VERSION, SCHEMA, SYSTEM
from evaluation.relevance.holdout import read_jsonl, text_hash
from evaluation.relevance.sample_holdout import make_sample
from evaluation.relevance.silver import MODEL_B, Reply, annotate, openrouter
from llm.chat import OPENROUTER_URL, Endpoint

A = Endpoint(provider="openrouter", url=OPENROUTER_URL, model="deepseek/deepseek-v4.1-flash")
B = Endpoint(provider="openrouter", url=OPENROUTER_URL, model=MODEL_B)
FIELD = "relevant_to_court_monitor"


def article(article_id: int) -> dict[str, Any]:
    body = f"Текст статьи {article_id} " * 300
    return {
        "article_id": article_id,
        "title": f"Заголовок {article_id}",
        "text": body,
        "source": "Медиазона",
        "url": f"https://example.org/{article_id}",
        "published_at": f"2026-09-{20 + article_id % 10:02d}T{article_id % 24:02d}:00:00Z",
        "text_hash": text_hash(body),
    }


class Fake:
    def __init__(self, relevant: bool, explanation: str = "") -> None:
        self.relevant, self.explanation = relevant, explanation
        self.sent: list[tuple[str, str]] = []

    def __call__(self, endpoint: Endpoint, user: str) -> Reply:
        self.sent.append((endpoint.model, user))
        labels = [
            {
                "id": item["id"],
                FIELD: self.relevant,
                "explanation": self.explanation or f"ответ {endpoint.model}",
            }
            for item in json.loads(user)
        ]
        return Reply(json.dumps({"labels": labels}), endpoint.model, 0.001)


def run(out: Path, endpoint: Endpoint, fake: Fake, articles: list[dict[str, Any]]) -> None:
    annotate(
        articles,
        out,
        annotator="a" if endpoint is A else "b",
        endpoint=endpoint,
        transport=fake,
        say=lambda _: None,
        task=TASK,
    )


# --- the annotators ------------------------------------------------------------------


def test_a_and_b_get_the_same_payload_and_nothing_of_each_other(tmp_path: Path) -> None:
    articles = [article(100 + i) for i in range(20)]
    fake_a, fake_b = Fake(True, "секрет A"), Fake(False)
    run(tmp_path / "labels_a.jsonl", A, fake_a, articles)
    run(tmp_path / "labels_b.jsonl", B, fake_b, articles)
    assert [user for _, user in fake_a.sent] == [user for _, user in fake_b.sent]
    assert all("секрет A" not in user and A.model not in user for _, user in fake_b.sent)


def test_the_request_asks_the_new_rubric(tmp_path: Path) -> None:
    captured: list[dict[str, Any]] = []

    def provider(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": MODEL_B,
                "choices": [{"message": {"content": '{"labels": []}'}, "finish_reason": "stop"}],
                "usage": {"cost": 0.0},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(provider)) as http:
        openrouter(http, TASK)(B, "[]")
    (body,) = captured
    assert body["messages"][0] == {"role": "system", "content": SYSTEM}
    assert body["response_format"]["json_schema"]["schema"] == SCHEMA
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["temperature"] == 0
    assert body["reasoning"] == {"enabled": False}
    properties = SCHEMA["properties"]["labels"]["items"]["properties"]  # type: ignore[index]
    assert set(properties) == {"id", FIELD, "explanation"}
    assert "kind" not in SYSTEM.split("Типичные true")[0]


def test_labels_keep_the_prompt_version_and_the_model(tmp_path: Path) -> None:
    out = tmp_path / "labels_b.jsonl"
    run(out, B, Fake(True), [article(1)])
    assert read_jsonl(out) == [
        {
            "article_id": 1,
            FIELD: True,
            "explanation": f"ответ {MODEL_B}",
            "annotator": "b",
            "model_id": MODEL_B,
            "answered_by": MODEL_B,
            "prompt_version": PROMPT_VERSION == "court-monitor-relevance-v1" and PROMPT_VERSION,
        }
    ]


def test_a_repeated_run_adds_nothing(tmp_path: Path) -> None:
    articles = [article(100 + i) for i in range(10)]
    out = tmp_path / "labels_a.jsonl"
    run(out, A, Fake(True), articles)
    before = out.read_bytes()
    again = Fake(False)
    run(out, A, again, articles)
    assert again.sent == []
    assert out.read_bytes() == before


def test_an_answer_off_the_new_schema_is_refused() -> None:
    with pytest.raises(ValueError):
        parse('{"labels": [{"id": 1, "kind": "case", "political": true, "explanation": "x"}]}')
    assert parse(f'{{"labels": [{{"id": 1, "{FIELD}": false, "explanation": "x"}}]}}') == [
        (1, {FIELD: False, "explanation": "x"})
    ]


# --- consensus -----------------------------------------------------------------------


def label(article_id: int, relevant: bool, explanation: str = "e", **extra: Any) -> dict[str, Any]:
    return {"article_id": article_id, FIELD: relevant, "explanation": explanation, **extra}


def test_consensus_is_the_boolean_alone() -> None:
    assert disagreement(label(1, True, "антивоенный пост"), label(1, True, "пикет")) is None
    assert disagreement(label(1, False), label(1, False)) is None
    assert disagreement(label(1, True), label(1, False)) == "relevance"
    assert disagreement(label(1, True), None) == "missing"


def test_old_kind_and_political_decide_nothing() -> None:
    articles = [article(1), article(2)]
    a = by_article(
        [
            label(1, False, kind="case", political=True),
            label(2, True, kind="other", political=False),
        ],
        "a",
    )
    b = by_article(
        [
            label(1, False, kind="other", political=False),
            label(2, True, kind="case", political=True),
        ],
        "b",
    )
    final, disagreements, _ = build(articles, a, b, {})
    assert [(f["article_id"], f[FIELD]) for f in final] == [(1, False), (2, True)]
    assert disagreements == []
    assert set(final[0]) == {"article_id", FIELD, "annotation_source"}


def test_a_disagreement_waits_for_a_person_who_then_wins() -> None:
    articles = [article(1), article(2), article(3)]
    a = by_article([label(1, True), label(2, True), label(3, False)], "a")
    b = by_article([label(1, True), label(2, False)], "b")
    final, disagreements, counts = build(articles, a, b, {})
    assert [f["article_id"] for f in final] == [1]
    assert [(d["article_id"], d["differs"]) for d in disagreements] == [
        (2, "relevance"),
        (3, "missing"),
    ]
    assert counts == counts | {
        "consensus": 1,
        "consensus_relevant": 1,
        "relevance": 1,
        "missing": 1,
        "remaining": 2,
        "relevant_a": 2,
        "relevant_b": 1,
    }
    human = {
        2: {"article_id": 2, FIELD: False, "annotation_source": "human_adjudication"},
        1: {"article_id": 1, FIELD: False, "annotation_source": "human_adjudication"},
    }
    final, _, counts = build(articles, a, b, human)
    assert [(f["article_id"], f[FIELD], f["annotation_source"]) for f in final] == [
        (1, False, "human_adjudication"),
        (2, False, "human_adjudication"),
    ]
    assert counts["remaining"] == 1


def test_a_label_without_the_boolean_is_refused() -> None:
    with pytest.raises(TypeError):
        by_article([{"article_id": 1, "kind": "case", "political": True}], "a")


# --- files, adjudication, audit ------------------------------------------------------


def holdout(tmp_path: Path, size: int = 200) -> tuple[Path, Path, list[int]]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    rows = [article(21000 + i) for i in range(size * 2)]
    (tmp_path / "articles.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )
    (tmp_path / "metadata.json").write_text(json.dumps({"frozen": True}), encoding="utf-8")
    make_sample(tmp_path, size=size, created_at=datetime(2026, 10, 15, tzinfo=UTC))
    out = tmp_path / "court_monitor_relevance"
    out.mkdir()
    ids = [a["article_id"] for a in read_jsonl(tmp_path / "sample_articles.jsonl")]
    return out, tmp_path / "sample_articles.jsonl", ids


def write_labels(out: Path, ids: list[int], differ: set[int]) -> None:
    for name, flip in (("labels_a.jsonl", False), ("labels_b.jsonl", True)):
        rows = [label(i, (i % 2 == 0) != (flip and i in differ)) for i in ids]
        (out / name).write_text("".join(json.dumps(r) + "\n" for r in rows))


def answers(*keys: str) -> Any:
    queue: Iterator[str] = iter(keys)
    return lambda prompt: next(queue)


def test_adjudication_by_hand(tmp_path: Path) -> None:
    out, sample, ids = holdout(tmp_path)
    write_labels(out, ids, {ids[2], ids[7]})
    shown: list[str] = []
    assert adjudicate(out, sample, ask=answers("?", "y", "n"), say=shown.append) == 2
    decided = {row["article_id"]: row[FIELD] for row in read_jsonl(out / "adjudications.jsonl")}
    assert decided == {ids[2]: True, ids[7]: False}
    final = {row["article_id"]: row for row in read_jsonl(out / "consensus_labels.jsonl")}
    assert len(final) == 200
    assert final[ids[2]]["annotation_source"] == "human_adjudication"
    screen = "\n".join(shown)
    assert "relevant_to_court_monitor" in screen
    assert "A: " in screen and "B: " in screen


def test_a_skipped_disagreement_is_in_no_final_label(tmp_path: Path) -> None:
    out, sample, ids = holdout(tmp_path)
    write_labels(out, ids, {ids[2]})
    adjudicate(out, sample, ask=answers("s"), say=lambda _: None)
    assert ids[2] not in {row["article_id"] for row in read_jsonl(out / "consensus_labels.jsonl")}


def test_the_audit_waits_then_is_fixed(tmp_path: Path) -> None:
    out, sample, ids = holdout(tmp_path)
    write_labels(out, ids, {ids[2]})
    with pytest.raises(ValueError, match="not decided"):
        freeze_audit(out, sample)
    write_labels(out, ids, set())
    first = freeze_audit(out, sample)
    assert first == freeze_audit(out, sample)
    assert len(first) == len(set(first)) == 50
    other, other_sample, other_ids = holdout(tmp_path / "again")
    write_labels(other, other_ids, set())
    assert freeze_audit(other, other_sample) == first


def test_the_audit_records_verdicts_and_the_rate(tmp_path: Path) -> None:
    out, sample, ids = holdout(tmp_path)
    write_labels(out, ids, set())
    first = freeze_audit(out, sample)
    consensus = {
        row["article_id"]: row[FIELD] for row in read_jsonl(out / "consensus_labels.jsonl")
    }
    keys = ["y" if consensus[first[0]] else "n", "n" if consensus[first[1]] else "y", "q"]
    assert audit(out, sample, ask=answers(*keys), say=lambda _: None) == 2
    verdicts = read_jsonl(out / "audit.jsonl")
    assert [v["verdict"] for v in verdicts] == ["agree", "disagree"]
    assert read_jsonl(out / "consensus_labels.jsonl")[0]["annotation_source"] == "llm_consensus"
    assert audit_agreement(verdicts) == {
        "checked": 2,
        "agree": 1,
        "disagree": 1,
        "agreement_rate": 0.5,
        "acceptable": False,
    }
    assert (
        audit_agreement([{"verdict": "agree"}] * 9 + [{"verdict": "disagree"}])["acceptable"]
        is True
    )


def test_the_old_experiment_is_never_read(tmp_path: Path) -> None:
    out, sample, ids = holdout(tmp_path)
    write_labels(out, ids, set())
    first = rebuild(out, sample)
    before = (out / "consensus_labels.jsonl").read_bytes()
    old = [{"article_id": i, "kind": "case", "political": True, "explanation": "x"} for i in ids]
    for name in ("labels_a.jsonl", "labels_b.jsonl", "sample_labels.jsonl"):
        (tmp_path / name).write_text("".join(json.dumps(r) + "\n" for r in old))
    assert rebuild(out, sample) == first
    assert (out / "consensus_labels.jsonl").read_bytes() == before


# --- no classifier, no model at decision time ----------------------------------------


def test_nothing_of_a_classifier_is_imported() -> None:
    code = (
        "import sys\n"
        "import evaluation.relevance.court_relevance\n"
        "import evaluation.relevance.court_relevance_consensus\n"
        "import evaluation.relevance.annotate_court_monitor_relevance\n"
        "print(','.join(m for m in sys.modules if m.startswith(('sklearn', 'numpy', "
        "'pymorphy3')) or m in ('evaluation.relevance.baseline', "
        "'evaluation.relevance.preprocessing', 'evaluation.relevance.ablation', "
        "'evaluation.relevance.analysis')))\n"
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


def test_consensus_and_the_human_tool_call_no_model() -> None:
    code = (
        "import sys\n"
        "import evaluation.relevance.court_relevance_consensus\n"
        "import evaluation.relevance.annotate_court_monitor_relevance\n"
        "print(','.join(m for m in sys.modules if m in ('llm.chat', 'httpx', "
        "'evaluation.relevance.silver', 'evaluation.junk_screen.labels')))\n"
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
