"""Decide by hand where the LLM annotators of `court-monitor-relevance-v1` differ, then
audit 50 of their consensus labels.

    python -m evaluation.relevance.annotate_court_monitor_relevance --disagreements
    python -m evaluation.relevance.annotate_court_monitor_relevance --audit

Keys: y relevant, n not relevant, s skip, q quit, ? the rubric. Each decision is saved at
once. No model is called, and nothing of `junk-labels-v1` is shown.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from evaluation.relevance.annotate_holdout import CHARS, _ask, show
from evaluation.relevance.court_relevance_consensus import (
    ADJUDICATION,
    CONSENSUS,
    FIELD,
    HOLDOUT_DIR,
    audit_agreement,
    freeze_audit,
    rebuild,
)
from evaluation.relevance.court_relevance_rubric import SYSTEM
from evaluation.relevance.holdout import read_jsonl, write_atomically
from evaluation.relevance.sample_holdout import check_frozen

KEYS = "y relevant, n not relevant, s skip, q quit, ? rubric"


def _yes_no(value: object) -> str:
    return "relevant" if value else "not relevant"


def _opinion(name: str, label: Mapping[str, Any] | None) -> str:
    if label is None:
        return f"{name}: no answer"
    return (
        f"{name}: {_yes_no(label[FIELD])} ({label.get('model_id')})\n"
        f"   {label.get('explanation') or ''}"
    )


def _decide(ask: Callable[[str], str], say: Callable[[str], None], prompt: str) -> str:
    while True:
        answer = _ask(prompt, ["y", "n", "s", "q", "?"], ask)
        if answer != "?":
            return answer
        say(SYSTEM)


def _rows(path: Path) -> list[dict[str, Any]]:
    return read_jsonl(path) if path.exists() else []


def _write(path: Path, rows: Mapping[int, Mapping[str, Any]], order: Sequence[int]) -> None:
    write_atomically(
        path,
        "".join(
            json.dumps(dict(rows[key]), ensure_ascii=False) + "\n" for key in order if key in rows
        ),
    )


def adjudicate(
    out_dir: Path,
    sample_path: Path,
    *,
    ask: Callable[[str], str] | None = None,
    say: Callable[[str], None] | None = None,
    chars: int = CHARS,
) -> int:
    """Decide the disagreements by hand; how many were decided."""
    ask = ask or input
    say = say or print
    rebuild(out_dir, sample_path)
    articles = {int(a["article_id"]): a for a in read_jsonl(sample_path)}
    path = out_dir / "adjudications.jsonl"
    decided = {int(row["article_id"]): row for row in _rows(path)}
    todo = [
        row
        for row in _rows(out_dir / "disagreements.jsonl")
        if int(row["article_id"]) not in decided
    ]
    say(f"{len(decided)} decided, {len(todo)} to go")
    done = 0
    for position, row in enumerate(todo, 1):
        article_id = int(row["article_id"])
        say(show(articles[article_id], chars))
        say(f"{_opinion('A', row['a'])}\n{_opinion('B', row['b'])}")
        answer = _decide(ask, say, f"[{position}/{len(todo)}] {KEYS}: ")
        if answer == "q":
            break
        if answer == "s":
            continue
        decided[article_id] = {
            "article_id": article_id,
            FIELD: answer == "y",
            "annotation_source": ADJUDICATION,
        }
        _write(path, decided, sorted(decided))
        done += 1
    counts = rebuild(out_dir, sample_path)
    say(f"{counts['adjudicated']} decided, {counts['remaining']} to go")
    return done


def audit(
    out_dir: Path,
    sample_path: Path,
    *,
    ask: Callable[[str], str] | None = None,
    say: Callable[[str], None] | None = None,
    chars: int = CHARS,
) -> int:
    """Check the audit's consensus labels by hand; how many were checked. A verdict is
    kept beside the label, never in place of it."""
    ask = ask or input
    say = say or print
    ids = freeze_audit(out_dir, sample_path)
    articles = {int(a["article_id"]): a for a in read_jsonl(sample_path)}
    labels = {int(row["article_id"]): row for row in read_jsonl(out_dir / "consensus_labels.jsonl")}
    path = out_dir / "audit.jsonl"
    verdicts = {int(row["article_id"]): row for row in _rows(path)}
    todo = [article_id for article_id in ids if article_id not in verdicts]
    say(f"{len(verdicts)} / {len(ids)} checked")
    done = 0
    for position, article_id in enumerate(todo, 1):
        label = labels[article_id]
        if label["annotation_source"] != CONSENSUS:
            raise ValueError(f"{article_id} is not a consensus label")
        say(show(articles[article_id], chars))
        say(f"consensus: {_yes_no(label[FIELD])}")
        answer = _decide(ask, say, f"[{position}/{len(todo)}] is it relevant? {KEYS}: ")
        if answer == "q":
            break
        if answer == "s":
            continue
        human = answer == "y"
        verdicts[article_id] = {
            "article_id": article_id,
            "consensus": label[FIELD],
            "human": human,
            "verdict": "agree" if human == label[FIELD] else "disagree",
        }
        _write(path, verdicts, ids)
        done += 1
    say(json.dumps(audit_agreement(list(verdicts.values()))))
    return done


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--dir", type=Path, default=HOLDOUT_DIR)
    parser.add_argument("--chars", type=int, default=CHARS)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--disagreements", action="store_true")
    mode.add_argument("--audit", action="store_true")
    args = parser.parse_args(argv)
    check_frozen(args.dir)
    out_dir, sample_path = args.dir / "court_monitor_relevance", args.dir / "sample_articles.jsonl"
    try:
        (adjudicate if args.disagreements else audit)(out_dir, sample_path, chars=args.chars)
    except (KeyboardInterrupt, EOFError):
        print("\nstopped; every decision given so far is saved")
    except ValueError as error:
        print(f"refused: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
