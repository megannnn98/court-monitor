"""Silver `relevant_to_court_monitor` labels of the holdout's sample: two LLM annotators.

    python -m evaluation.relevance.court_relevance a    # deepseek/deepseek-v4.1-flash
    python -m evaluation.relevance.court_relevance b    # anthropic/claude-haiku-4.5

The same machinery as `silver` — the same batches of 8, payload, strict schema,
temperature 0, no reasoning, no fallback — with `court-monitor-relevance-v1` asked instead
of `junk-labels-v1`. The labels go to their own folder; the old ones are never read.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field

from evaluation.relevance.court_relevance_rubric import PROMPT_VERSION, SCHEMA, SYSTEM
from evaluation.relevance.holdout import read_jsonl
from evaluation.relevance.sample_holdout import check_frozen
from evaluation.relevance.silver import FILES, Task, annotate, endpoints, openrouter, record_run

ROOT = Path(__file__).resolve().parents[3]
HOLDOUT_DIR = ROOT / "data/ml/relevance/holdout"
OUT_DIR = HOLDOUT_DIR / "court_monitor_relevance"


class _Label(BaseModel):
    id: int
    relevant_to_court_monitor: bool
    explanation: str = Field(max_length=500)


class _Labels(BaseModel):
    labels: list[_Label]


def parse(content: str) -> list[tuple[int, dict[str, Any]]]:
    return [
        (
            answer.id,
            {
                "relevant_to_court_monitor": answer.relevant_to_court_monitor,
                "explanation": answer.explanation,
            },
        )
        for answer in _Labels.model_validate_json(content).labels
    ]


TASK = Task(PROMPT_VERSION, SYSTEM, SCHEMA, parse)


def run_annotator(
    annotator: str,
    articles: Sequence[Mapping[str, Any]],
    out_dir: Path,
    fixed: Mapping[str, Any],
) -> int:
    """One annotator over `articles` into `out_dir`, the run kept in its summary; 0 when
    it ran without a provider error. The same for the holdout and any other corpus."""
    chosen = endpoints()
    out_dir.mkdir(exist_ok=True)
    started = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    with httpx.Client() as http:
        run = annotate(
            articles,
            out_dir / FILES[annotator],
            annotator=annotator,
            endpoint=chosen[annotator],
            transport=openrouter(http, TASK),
            task=TASK,
        )
    summary = record_run(
        out_dir / "annotation_run.json",
        {**run, "started_at": started},
        {
            "model_a": chosen["a"].model,
            "model_b": chosen["b"].model,
            "prompt_version": PROMPT_VERSION,
            "article_count": len(articles),
            **fixed,
        },
    )
    print(json.dumps({**run, f"cost_{annotator}_usd_total": summary[f"cost_{annotator}_usd"]}))
    if run["error"]:
        print(f"{annotator}: stopped, nothing falls back: {run['error']}", file=sys.stderr)
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or args[0] not in FILES:
        print("usage: python -m evaluation.relevance.court_relevance a|b", file=sys.stderr)
        return 2
    sample = check_frozen(HOLDOUT_DIR)
    return run_annotator(
        args[0],
        read_jsonl(HOLDOUT_DIR / "sample_articles.jsonl"),
        OUT_DIR,
        {"sample_sha256": sample["sample_sha256"]},
    )


if __name__ == "__main__":
    raise SystemExit(main())
