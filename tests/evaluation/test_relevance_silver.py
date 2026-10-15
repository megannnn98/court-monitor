"""Silver labels of the relevance sample: two LLM annotators on their own, consensus where
they agree, a person where they do not, and a fixed audit of the consensus. No network:
the provider is faked."""

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

from evaluation.junk_screen.labels import PROMPT_VERSION, SCHEMA, SYSTEM, TEXT_LIMIT
from evaluation.relevance.annotate_holdout import adjudicate, audit
from evaluation.relevance.annotate_holdout import main as annotate_main
from evaluation.relevance.consensus import (
    audit_agreement,
    audit_sample,
    build,
    by_article,
    disagreement,
    freeze_audit,
    rebuild,
)
from evaluation.relevance.holdout import ordered, read_jsonl, text_hash
from evaluation.relevance.sample_holdout import make_sample
from evaluation.relevance.silver import (
    MODEL_B,
    Reply,
    annotate,
    batches,
    openrouter,
    payload,
    record_run,
    same_model,
)
from llm.chat import OPENROUTER_URL, Endpoint, ModelError

A = Endpoint(provider="openrouter", url=OPENROUTER_URL, model="deepseek/deepseek-v4.1-flash")
B = Endpoint(provider="openrouter", url=OPENROUTER_URL, model=MODEL_B)


def article(article_id: int, text: str = "") -> dict[str, Any]:
    body = text or f"Текст статьи {article_id} " * 300
    return {
        "article_id": article_id,
        "title": f"Заголовок {article_id}",
        "text": body,
        "source": "Медиазона",
        "url": f"https://example.org/{article_id}",
        "published_at": f"2026-09-{20 + article_id % 10:02d}T{article_id % 24:02d}:00:00Z",
        "text_hash": text_hash(body),
    }


def sample(count: int = 20) -> list[dict[str, Any]]:
    return [article(100 + i) for i in range(count)]


class Fake:
    """A provider that answers every article with the same decision, and records what it
    was sent."""

    def __init__(
        self, kind: str = "case", political: bool = False, model: str | None = None
    ) -> None:
        self.kind, self.political, self.model = kind, political, model
        self.sent: list[tuple[str, str]] = []

    def __call__(self, endpoint: Endpoint, user: str) -> Reply:
        self.sent.append((endpoint.model, user))
        labels = [
            {
                "id": item["id"],
                "kind": self.kind,
                "political": self.political,
                "explanation": f"ответ {endpoint.model}",
            }
            for item in json.loads(user)
        ]
        return Reply(json.dumps({"labels": labels}), self.model or endpoint.model, 0.001)


def run(tmp_path: Path, endpoint: Endpoint, fake: Fake, articles: list[dict[str, Any]]) -> Path:
    out = tmp_path / ("labels_a.jsonl" if endpoint is A else "labels_b.jsonl")
    annotate(
        articles,
        out,
        annotator="a" if endpoint is A else "b",
        endpoint=endpoint,
        transport=fake,
        say=lambda _: None,
    )
    return out


# --- the annotators ------------------------------------------------------------------


def test_b_gets_exactly_what_a_got_and_nothing_of_a(tmp_path: Path) -> None:
    articles = sample()
    fake_a, fake_b = Fake("case", True), Fake("other", False)
    run(tmp_path, A, fake_a, articles)
    run(tmp_path, B, fake_b, articles)
    assert [user for _, user in fake_a.sent] == [user for _, user in fake_b.sent]
    for _, user in fake_b.sent:
        assert "ответ" not in user
        assert A.model not in user
    assert {model for model, _ in fake_b.sent} == {MODEL_B}


def test_the_request_is_junk_labels_v1(tmp_path: Path) -> None:
    articles = sample(10)
    assert [len(batch) for batch in batches(articles)] == [8, 2]
    sent = json.loads(payload(batches(articles)[0]))
    assert set(sent[0]) == {"id", "title", "text"}
    assert all(len(item["text"]) == TEXT_LIMIT for item in sent)
    captured: list[dict[str, Any]] = []

    def provider(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": MODEL_B,
                "choices": [{"message": {"content": '{"labels": []}'}, "finish_reason": "stop"}],
                "usage": {"cost": 0.002},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(provider)) as http:
        reply = openrouter(http)(B, "[]")
    (body,) = captured
    assert body["model"] == MODEL_B
    assert body["messages"] == [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": "[]"},
    ]
    assert body["response_format"]["json_schema"] == {
        "name": "labels",
        "strict": True,
        "schema": SCHEMA,
    }
    assert body["temperature"] == 0
    assert body["reasoning"] == {"enabled": False}
    assert body["provider"] == {"require_parameters": True}
    assert reply == Reply('{"labels": []}', MODEL_B, 0.002)


def test_a_provider_refusing_the_schema_is_an_error(tmp_path: Path) -> None:
    def refusing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"message": "No endpoints found"}})

    with (
        httpx.Client(transport=httpx.MockTransport(refusing)) as http,
        pytest.raises(ModelError, match="HTTP 404"),
    ):
        openrouter(http)(B, "[]")


def test_a_run_stops_on_an_error_and_never_falls_back(tmp_path: Path) -> None:
    calls: list[str] = []

    def failing(endpoint: Endpoint, user: str) -> Reply:
        calls.append(endpoint.model)
        raise ModelError("HTTP 404: no endpoint supports the schema")

    summary = annotate(
        sample(),
        tmp_path / "labels_b.jsonl",
        annotator="b",
        endpoint=B,
        transport=failing,
        say=lambda _: None,
    )
    assert calls == [MODEL_B]
    assert summary["error"] == "HTTP 404: no endpoint supports the schema"
    assert not (tmp_path / "labels_b.jsonl").exists()


def test_an_answer_by_another_model_stops_the_run(tmp_path: Path) -> None:
    summary = annotate(
        sample(),
        tmp_path / "labels_b.jsonl",
        annotator="b",
        endpoint=B,
        transport=Fake(model="openai/gpt-4.1-mini"),
        say=lambda _: None,
    )
    assert "answered by openai/gpt-4.1-mini" in summary["error"]
    assert not (tmp_path / "labels_b.jsonl").exists()
    assert same_model(MODEL_B, f"{MODEL_B}-20261001")
    assert not same_model(MODEL_B, "anthropic/claude-haiku-5")
    assert not same_model(MODEL_B, None)


def test_labels_keep_who_gave_them(tmp_path: Path) -> None:
    out = run(tmp_path, B, Fake("administrative", True, model=f"{MODEL_B}-20261001"), sample(3))
    assert read_jsonl(out)[0] == {
        "article_id": 100,
        "kind": "administrative",
        "political": True,
        "explanation": f"ответ {MODEL_B}",
        "annotator": "b",
        "model_id": MODEL_B,
        "answered_by": f"{MODEL_B}-20261001",
        "prompt_version": PROMPT_VERSION,
    }


def test_a_repeated_run_asks_nothing_and_duplicates_nothing(tmp_path: Path) -> None:
    articles = sample()
    out = run(tmp_path, A, Fake(), articles)
    before = out.read_bytes()
    again = Fake("other")
    run(tmp_path, A, again, articles)
    assert again.sent == []
    assert out.read_bytes() == before
    ids = [row["article_id"] for row in read_jsonl(out)]
    assert len(ids) == len(set(ids)) == 20


def test_a_stopped_run_goes_on_with_what_is_missing(tmp_path: Path) -> None:
    articles = sample()
    out = tmp_path / "labels_a.jsonl"
    annotate(
        articles,
        out,
        annotator="a",
        endpoint=A,
        transport=Fake(),
        budget_usd=0.0015,
        say=lambda _: None,
    )
    assert len(read_jsonl(out)) == 16
    rest = Fake()
    annotate(articles, out, annotator="a", endpoint=A, transport=rest, say=lambda _: None)
    assert len(rest.sent) == 1
    assert [item["id"] for item in json.loads(rest.sent[0][1])] == [
        a["article_id"] for a in ordered(articles)[16:]
    ]
    assert len(read_jsonl(out)) == 20


def test_an_answer_off_the_schema_is_asked_again_next_run(tmp_path: Path) -> None:
    def broken(endpoint: Endpoint, user: str) -> Reply:
        return Reply('{"labels": [{"id": 1}]}', endpoint.model, 0.0)

    out = tmp_path / "labels_a.jsonl"
    summary = annotate(
        sample(8), out, annotator="a", endpoint=A, transport=broken, say=lambda _: None
    )
    assert summary["missing"] == 8
    assert summary["error"] is None


def test_the_run_summary_adds_up_the_spend(tmp_path: Path) -> None:
    path = tmp_path / "annotation_run.json"
    fixed = {
        "model_a": A.model,
        "model_b": MODEL_B,
        "prompt_version": PROMPT_VERSION,
        "article_count": 400,
        "sample_sha256": "abc",
    }
    record_run(path, {"annotator": "a", "cost_usd": 0.01, "started_at": "t1"}, fixed)
    summary = record_run(path, {"annotator": "a", "cost_usd": 0.02, "started_at": "t2"}, fixed)
    record_run(path, {"annotator": "b", "cost_usd": 0.05, "started_at": "t3"}, fixed)
    summary = json.loads(path.read_text(encoding="utf-8"))
    assert summary["cost_a_usd"] == pytest.approx(0.03)
    assert summary["cost_b_usd"] == pytest.approx(0.05)
    assert summary["sample_sha256"] == "abc"
    with pytest.raises(ValueError, match="sample_sha256"):
        record_run(path, {"annotator": "a", "cost_usd": 0}, {**fixed, "sample_sha256": "x"})


# --- consensus -----------------------------------------------------------------------


def label(article_id: int, kind: str, political: bool) -> dict[str, Any]:
    return {"article_id": article_id, "kind": kind, "political": political, "explanation": "e"}


def test_agreement_is_both_fields() -> None:
    assert disagreement(label(1, "case", True), label(1, "case", True)) is None
    assert disagreement(label(1, "case", True), label(1, "case", False)) == "political_only"
    assert disagreement(label(1, "case", True), label(1, "other", True)) == "kind"
    assert disagreement(label(1, "case", False), label(1, "other", True)) == "kind"
    assert disagreement(label(1, "case", True), None) == "missing"


def test_consensus_and_disagreements() -> None:
    articles = sample(5)
    a = by_article(
        [
            label(100, "case", True),
            label(101, "case", True),
            label(102, "case", False),
            label(103, "administrative", True),
        ],
        "a",
    )
    b = by_article(
        [
            label(100, "case", True),
            label(101, "case", False),
            label(102, "other", False),
            label(103, "administrative", True),
            label(104, "other", False),
        ],
        "b",
    )
    final, disagreements, counts = build(articles, a, b, {})
    assert final == [
        {
            "article_id": 100,
            "kind": "case",
            "political": True,
            "annotation_source": "llm_consensus",
        },
        {
            "article_id": 103,
            "kind": "administrative",
            "political": True,
            "annotation_source": "llm_consensus",
        },
    ]
    assert [(d["article_id"], d["differs"]) for d in disagreements] == [
        (101, "political_only"),
        (102, "kind"),
        (104, "missing"),
    ]
    assert disagreements[0]["a"]["kind"] == "case"
    assert counts == counts | {
        "consensus": 2,
        "kind": 1,
        "political_only": 1,
        "missing": 1,
        "remaining": 3,
        "adjudicated": 0,
        "labelled_a": 4,
        "labelled_b": 5,
    }


def test_a_person_decides_and_always_wins() -> None:
    articles = sample(2)
    a = by_article([label(100, "case", True), label(101, "case", True)], "a")
    b = by_article([label(100, "case", True), label(101, "other", False)], "b")
    human = {
        101: {
            "article_id": 101,
            "kind": "past_only",
            "political": True,
            "annotation_source": "human_adjudication",
        },
        100: {
            "article_id": 100,
            "kind": "other",
            "political": False,
            "annotation_source": "human_adjudication",
        },
    }
    final, disagreements, counts = build(articles, a, b, human)
    assert [(f["article_id"], f["kind"], f["annotation_source"]) for f in final] == [
        (100, "other", "human_adjudication"),
        (101, "past_only", "human_adjudication"),
    ]
    assert counts["remaining"] == 0
    assert [d["article_id"] for d in disagreements] == [101]


def test_labels_of_another_rubric_or_sample_are_refused() -> None:
    with pytest.raises(ValueError, match="unknown kind"):
        by_article([label(1, "skip", False)], "a")
    with pytest.raises(ValueError, match="twice"):
        by_article([label(1, "case", False), label(1, "case", False)], "a")
    with pytest.raises(ValueError, match="outside the sample"):
        build(sample(1), {7: label(7, "case", False)}, {}, {})


# --- files, adjudication, audit ------------------------------------------------------


def holdout(tmp_path: Path, size: int = 200) -> Path:
    rows = [article(21000 + i) for i in range(size * 2)]
    (tmp_path / "articles.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )
    (tmp_path / "metadata.json").write_text(json.dumps({"frozen": True}), encoding="utf-8")
    make_sample(tmp_path, size=size, created_at=datetime(2026, 10, 15, tzinfo=UTC))
    return tmp_path


def label_files(directory: Path, differ: set[int]) -> list[int]:
    ids = [a["article_id"] for a in read_jsonl(directory / "sample_articles.jsonl")]
    rows_a = [label(i, "case", True) for i in ids]
    rows_b = [label(i, "other" if i in differ else "case", True) for i in ids]
    for name, rows in (("labels_a.jsonl", rows_a), ("labels_b.jsonl", rows_b)):
        (directory / name).write_text("".join(json.dumps(r) + "\n" for r in rows))
    return ids


def answers(*keys: str) -> Any:
    queue: Iterator[str] = iter(keys)
    return lambda prompt: next(queue)


def test_adjudication_decides_only_the_disagreements(tmp_path: Path) -> None:
    directory = holdout(tmp_path)
    ids = label_files(directory, set())
    differ = {ids[3], ids[10]}
    label_files(directory, differ)
    shown: list[str] = []
    done = adjudicate(directory, ask=answers("a", "y", "p", "n"), say=shown.append)
    assert done == 2
    decided = read_jsonl(directory / "adjudications.jsonl")
    assert {d["article_id"]: (d["kind"], d["political"]) for d in decided} == {
        ids[3]: ("administrative", True),
        ids[10]: ("past_only", False),
    }
    final = {row["article_id"]: row for row in read_jsonl(directory / "sample_labels.jsonl")}
    assert len(final) == 200
    assert final[ids[3]]["annotation_source"] == "human_adjudication"
    assert final[ids[0]]["annotation_source"] == "llm_consensus"
    screen = "\n".join(shown)
    assert "A: case, political=true" in screen and "B: other, political=true" in screen


def test_an_undecided_disagreement_stays_out_of_the_labels(tmp_path: Path) -> None:
    directory = holdout(tmp_path)
    ids = label_files(directory, set())
    label_files(directory, {ids[5]})
    adjudicate(directory, ask=answers("s"), say=lambda _: None)
    final = {row["article_id"] for row in read_jsonl(directory / "sample_labels.jsonl")}
    assert ids[5] not in final
    assert len(final) == 199
    assert not (directory / "adjudications.jsonl").exists()


def test_the_audit_waits_for_every_disagreement(tmp_path: Path) -> None:
    directory = holdout(tmp_path)
    ids = label_files(directory, set())
    label_files(directory, {ids[5]})
    with pytest.raises(ValueError, match="1 disagreements are not decided"):
        freeze_audit(directory)
    assert not (directory / "consensus_audit_sample.json").exists()


def test_the_audit_sample_is_fixed(tmp_path: Path) -> None:
    population = list(range(1, 300))
    assert audit_sample(population) == audit_sample(list(reversed(population)))
    assert len(set(audit_sample(population))) == 50
    assert audit_sample(population, seed=1) != audit_sample(population)
    directory = holdout(tmp_path)
    label_files(directory, set())
    first = freeze_audit(directory)
    second = freeze_audit(directory)
    assert first == second
    assert len(first) == 50


def test_the_audit_records_verdicts_beside_the_labels(tmp_path: Path) -> None:
    directory = holdout(tmp_path)
    label_files(directory, set())
    before = (directory / "labels_a.jsonl").read_bytes()
    done = audit(directory, ask=answers("y", "n", "f", "y", "q"), say=lambda _: None)
    assert done == 2
    verdicts = read_jsonl(directory / "consensus_audit.jsonl")
    assert verdicts[0]["verdict"] == "agree"
    assert verdicts[1] | {} == {
        "article_id": verdicts[1]["article_id"],
        "consensus_kind": "case",
        "consensus_political": True,
        "verdict": "disagree",
        "kind": "foreign",
        "political": True,
    }
    final = {row["article_id"]: row for row in read_jsonl(directory / "sample_labels.jsonl")}
    assert final[verdicts[1]["article_id"]]["kind"] == "case"
    assert (directory / "labels_a.jsonl").read_bytes() == before
    assert audit_agreement(verdicts)["agreement_rate"] == 0.5
    audit(directory, ask=answers("q"), say=lambda _: None)
    assert len(read_jsonl(directory / "consensus_audit.jsonl")) == 2


def test_manual_sample_labelling_is_closed_once_the_llms_labelled(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = holdout(tmp_path)
    label_files(directory, set())
    rebuild(directory)
    before = (directory / "sample_labels.jsonl").read_bytes()
    assert annotate_main(["--dir", str(directory), "--sample"]) == 1
    assert (directory / "sample_labels.jsonl").read_bytes() == before


# --- no classifier -------------------------------------------------------------------


def test_the_annotators_and_consensus_import_no_classifier() -> None:
    code = (
        "import sys\n"
        "import evaluation.relevance.silver, evaluation.relevance.consensus\n"
        "bad = [m for m in sys.modules if m.startswith(('sklearn', 'numpy', 'pymorphy3')) "
        "or m in ('evaluation.relevance.baseline', 'evaluation.relevance.preprocessing', "
        "'evaluation.relevance.ablation', 'evaluation.relevance.analysis')]\n"
        "print(','.join(bad))\n"
        "import evaluation.relevance.consensus as c\n"
        "print('llm.chat' in sys.modules and 'consensus-ok')\n"
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
    assert result.stdout.splitlines()[0] == ""


def test_consensus_and_human_checks_call_no_model() -> None:
    code = (
        "import sys\n"
        "import evaluation.relevance.consensus, evaluation.relevance.annotate_holdout\n"
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


def test_a_model_of_a_similar_name_is_another_model() -> None:
    assert not same_model(MODEL_B, f"{MODEL_B}5")


def test_answers_for_articles_not_asked_are_dropped(tmp_path: Path) -> None:
    def chatty(endpoint: Endpoint, user: str) -> Reply:
        asked = [item["id"] for item in json.loads(user)]
        labels = [
            {"id": i, "kind": "other", "political": False, "explanation": "e"}
            for i in [*asked, 999]
        ]
        labels.append({"id": asked[0], "kind": "case", "political": True, "explanation": "e"})
        return Reply(json.dumps({"labels": labels}), endpoint.model, 0.0)

    out = tmp_path / "labels_a.jsonl"
    annotate(sample(3), out, annotator="a", endpoint=A, transport=chatty, say=lambda _: None)
    rows = read_jsonl(out)
    assert sorted(row["article_id"] for row in rows) == [100, 101, 102]
    assert {row["kind"] for row in rows} == {"other"}


def test_the_audit_refuses_a_label_that_is_no_longer_consensus(tmp_path: Path) -> None:
    directory = holdout(tmp_path)
    label_files(directory, set())
    first = freeze_audit(directory)[0]
    (directory / "adjudications.jsonl").write_text(
        json.dumps(
            {
                "article_id": first,
                "kind": "other",
                "political": False,
                "annotation_source": "human_adjudication",
            }
        )
        + "\n"
    )
    rebuild(directory)
    with pytest.raises(ValueError, match="not a consensus label"):
        audit(directory, ask=answers("y"), say=lambda _: None)
