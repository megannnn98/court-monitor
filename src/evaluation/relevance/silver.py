"""Silver labels for the relevance holdout's sample: two LLM annotators, each on its own.

    python -m evaluation.relevance.silver a    # the current OpenRouter model (DeepSeek)
    python -m evaluation.relevance.silver b    # anthropic/claude-haiku-4.5

Both get exactly `junk-labels-v1`: its SYSTEM prompt, its JSON schema, its batches of 8,
each article's id, title and first 2500 characters — the same request but for the model's
name. Neither sees the other's answers, nor anything of a classifier.

A run labels only the articles its file does not hold yet, writes the file whole after
every batch, and stops at the first provider error — a route without the strict schema
included — rather than falling back to another model. A provider answering for a model
other than the one asked for is such an error too.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from evaluation.junk_screen.labels import BATCH, PROMPT_VERSION, SCHEMA, SYSTEM, TEXT_LIMIT, Labels
from evaluation.relevance.holdout import ordered, read_jsonl, write_atomically
from evaluation.relevance.sample_holdout import check_frozen
from llm.chat import (
    OPENROUTER_URL,
    TIMEOUT_SECONDS,
    Endpoint,
    ModelError,
    Spend,
    endpoint_from_env,
)
from monitor_core.llm.chat import post_json_chat, read_chat_completion

ROOT = Path(__file__).resolve().parents[3]
HOLDOUT_DIR = ROOT / "data/ml/relevance/holdout"
MODEL_B = "anthropic/claude-haiku-4.5"
BUDGET_USD = 1.0
# As `junk-labels-v1` asks.
MAX_TOKENS = 2000
FILES = {"a": "labels_a.jsonl", "b": "labels_b.jsonl"}
RUN_SUMMARY = "annotation_run.json"


@dataclass(frozen=True)
class Reply:
    content: str
    model: str | None
    cost_usd: float


Transport = Callable[[Endpoint, str], Reply]


@dataclass(frozen=True)
class Task:
    """What the annotators are asked: a system prompt, a strict schema, and how one
    answer turns into the fields a label keeps. `junk-labels-v1` by default."""

    prompt_version: str
    system: str
    schema: dict[str, object]
    # The answer's text → each article's id with the fields to keep; ValueError if the
    # answer is not the schema.
    parse: Callable[[str], list[tuple[int, dict[str, Any]]]]


def _junk_labels(content: str) -> list[tuple[int, dict[str, Any]]]:
    return [
        (
            answer.id,
            {"kind": answer.kind, "political": answer.political, "explanation": answer.explanation},
        )
        for answer in Labels.model_validate_json(content).labels
    ]


JUNK_LABELS = Task(PROMPT_VERSION, SYSTEM, SCHEMA, _junk_labels)


def payload(batch: Sequence[Mapping[str, Any]]) -> str:
    """The user message of `junk-labels-v1` for a batch: id, title, the text's start."""
    return json.dumps(
        [
            {"id": item["article_id"], "title": item["title"], "text": item["text"][:TEXT_LIMIT]}
            for item in batch
        ],
        ensure_ascii=False,
    )


def batches(articles: Sequence[Mapping[str, Any]]) -> list[list[dict[str, Any]]]:
    """The sample in publication order, cut into `junk-labels-v1`'s batches."""
    rows = ordered(articles)
    return [rows[start : start + BATCH] for start in range(0, len(rows), BATCH)]


def openrouter(http: httpx.Client, task: Task = JUNK_LABELS) -> Transport:
    """One request of `task`, as `llm.chat.chat_json` makes it — strict schema,
    only providers honouring it, no reasoning, temperature 0 — keeping the model the
    provider says answered."""

    def send(endpoint: Endpoint, user: str) -> Reply:
        try:
            response = post_json_chat(
                http,
                endpoint.url,
                model=endpoint.model,
                system=task.system,
                user=user,
                schema_name="labels",
                schema=task.schema,
                headers={"Authorization": f"Bearer {endpoint.api_key}"},
                timeout_seconds=TIMEOUT_SECONDS,
                strict=True,
                extra_body={
                    "max_tokens": MAX_TOKENS,
                    "provider": {"require_parameters": True},
                    "reasoning": {"enabled": False},
                    "usage": {"include": True},
                },
            )
        except httpx.HTTPError as exc:
            raise ModelError(f"{type(exc).__name__}: {exc}") from exc
        if response.status_code >= 400:
            raise ModelError(f"HTTP {response.status_code}: {response.text[:300]}")
        completion = read_chat_completion(response)
        if completion.finish_reason not in (None, "stop"):
            raise ModelError(f"unusable answer: finish_reason={completion.finish_reason}")
        if not isinstance(completion.content, str):
            raise ModelError("unusable answer: no content")
        usage = completion.usage if isinstance(completion.usage, dict) else {}
        return Reply(
            content=completion.content,
            model=completion.model if isinstance(completion.model, str) else None,
            cost_usd=float(usage.get("cost") or 0.0),
        )

    return send


def same_model(asked: str, answered: str | None) -> bool:
    """A provider names the model it ran, possibly with a dated suffix of its own."""
    return answered is not None and (answered == asked or answered.startswith(f"{asked}-"))


def annotate(
    articles: Sequence[Mapping[str, Any]],
    out: Path,
    *,
    annotator: str,
    endpoint: Endpoint,
    transport: Transport,
    budget_usd: float = BUDGET_USD,
    say: Callable[[str], None] = print,
    task: Task = JUNK_LABELS,
) -> dict[str, Any]:
    """Label the articles `out` does not hold yet; a summary of the run."""
    held = {int(row["article_id"]): row for row in read_jsonl(out)} if out.exists() else {}
    known = {int(article["article_id"]) for article in articles}
    if not set(held) <= known:
        raise ValueError(f"{out.name} holds articles outside the sample")
    spend = Spend(budget_usd)
    asked = 0
    error: str | None = None
    for batch in batches(articles):
        todo = [item for item in batch if int(item["article_id"]) not in held]
        if not todo:
            continue
        if spend.exhausted():
            say(f"budget ${budget_usd} spent; the rest waits for the next run")
            break
        # The batch is the one `junk-labels-v1` makes; only what is still missing is sent.
        try:
            reply = transport(endpoint, payload(todo))
        except ModelError as exc:
            error = str(exc)
            break
        spend.add(reply.cost_usd)
        asked += 1
        if not same_model(endpoint.model, reply.model):
            error = f"asked {endpoint.model}, answered by {reply.model}"
            break
        try:
            answers = task.parse(reply.content)
        except ValueError:
            say(f"batch of {len(todo)}: the answer is not the schema; asked again next run")
            continue
        wanted = {int(item["article_id"]) for item in todo}
        for answer_id, fields in answers:
            if answer_id in wanted and answer_id not in held:
                held[answer_id] = {
                    "article_id": answer_id,
                    **fields,
                    "annotator": annotator,
                    "model_id": endpoint.model,
                    "answered_by": reply.model,
                    "prompt_version": task.prompt_version,
                }
        write_atomically(
            out,
            "".join(
                json.dumps(held[int(a["article_id"])], ensure_ascii=False) + "\n"
                for a in ordered(articles)
                if int(a["article_id"]) in held
            ),
        )
        say(f"{annotator}: {len(held)} / {len(articles)} labelled, ${spend.cost_usd:.4f}")
    return {
        "annotator": annotator,
        "model_id": endpoint.model,
        "labelled": len(held),
        "missing": len(known - set(held)),
        "batches_asked": asked,
        "cost_usd": spend.cost_usd,
        "error": error,
    }


def record_run(path: Path, run: Mapping[str, Any], fixed: Mapping[str, Any]) -> dict[str, Any]:
    """The run summary file: what every run was given and spent."""
    summary: dict[str, Any] = (
        json.loads(path.read_text(encoding="utf-8")) if path.exists() else {**fixed, "runs": []}
    )
    for key, value in fixed.items():
        if summary.get(key) not in (None, value):
            raise ValueError(f"{path.name}: {key} was {summary[key]!r}, now {value!r}")
        summary[key] = value
    summary["runs"].append(dict(run))
    for annotator in FILES:
        summary[f"cost_{annotator}_usd"] = sum(
            r["cost_usd"] for r in summary["runs"] if r["annotator"] == annotator
        )
    write_atomically(path, json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    return summary


def endpoints(env: Mapping[str, str] | None = None) -> dict[str, Endpoint]:
    a = endpoint_from_env(env)
    if a is None:
        raise SystemExit("OPENROUTER_API_KEY is not set")
    b = Endpoint(provider="openrouter", url=OPENROUTER_URL, model=MODEL_B, api_key=a.api_key)
    return {"a": a, "b": b}


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or args[0] not in FILES:
        print("usage: python -m evaluation.relevance.silver a|b", file=sys.stderr)
        return 2
    annotator = args[0]
    sample = check_frozen(HOLDOUT_DIR)
    articles = read_jsonl(HOLDOUT_DIR / "sample_articles.jsonl")
    chosen = endpoints()
    started = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    with httpx.Client() as http:
        run = annotate(
            articles,
            HOLDOUT_DIR / FILES[annotator],
            annotator=annotator,
            endpoint=chosen[annotator],
            transport=openrouter(http),
        )
    summary = record_run(
        HOLDOUT_DIR / RUN_SUMMARY,
        {**run, "started_at": started},
        {
            "model_a": chosen["a"].model,
            "model_b": chosen["b"].model,
            "prompt_version": PROMPT_VERSION,
            "article_count": len(articles),
            "sample_sha256": sample["sample_sha256"],
        },
    )
    print(json.dumps({**run, f"cost_{annotator}_usd_total": summary[f"cost_{annotator}_usd"]}))
    if run["error"]:
        print(f"{annotator}: stopped, nothing falls back: {run['error']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
