"""Label the relevance holdout, or its fixed sample, by hand.

    python -m evaluation.relevance.annotate_holdout [--sample] [--chars 1500]
    python -m evaluation.relevance.annotate_holdout --disagreements
    python -m evaluation.relevance.annotate_holdout --audit

Shows the next unlabelled article in publication order — id, date, source, title, the
start of its text — and takes one key: c case, a administrative, p past_only, f foreign,
n crime_no_case, o other, s skip, q quit, ? the rubric. A case is asked whether it is
political. Each label is saved at once, atomically; a skip is not saved, so the article
comes back next time. No model, no probability, no earlier prediction is ever shown.

With `--sample` it labels `sample_articles.jsonl` into `sample_labels.jsonl` only. There a
label given by hand is also given to every other article of the sample with the very same
normalized text (`text_hash`), marked `duplicate_propagation` with the article it came
from — nothing fuzzy, and a skip is never propagated.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from evaluation.relevance.consensus import (
    ADJUDICATION,
    CONSENSUS,
    audit_agreement,
    freeze_audit,
    rebuild,
)
from evaluation.relevance.holdout import KINDS, ordered, read_jsonl, write_atomically
from evaluation.relevance.sample_holdout import check_frozen

ROOT = Path(__file__).resolve().parents[3]
HOLDOUT_DIR = ROOT / "data/ml/relevance/holdout"
CHARS = 1500
HUMAN = "human"
PROPAGATED = "duplicate_propagation"

COMMANDS = {
    "c": "case",
    "a": "administrative",
    "p": "past_only",
    "f": "foreign",
    "n": "crime_no_case",
    "o": "other",
}

# The definitions of `junk-labels-v1`, word for word.
RUBRIC = """\
c case — статья сообщает СВЕЖУЮ процессуальную новость по РОССИЙСКОМУ УГОЛОВНОМУ делу против
  конкретного человека (названного или безымянного: «17-летний житель Тюмени»): возбуждено
  дело, задержан, обыск, арестован (мера пресечения), предъявлено обвинение, объявлен в
  розыск, суд, приговор (в т. ч. заочно), продление ареста, апелляция. Любые статьи УК, не
  только политические;
a administrative — только административное: штраф по КоАП, административный арест, протокол;
p past_only — дело давнее и новостью не является: письма, акции поддержки, интервью,
  годовщина, упоминание прошлого приговора;
f foreign — преследуют не российские власти (Украина, Беларусь, другие страны);
n crime_no_case — преступление или происшествие без дела против конкретного человека,
  статистика, законопроект, общие слова о репрессиях;
o other — ничего из этого.
political (только для case): y, если это преследование по политическим мотивам (антивоенные
  посты, «фейки», «дискредитация», госизмена, экстремизм и терроризм за высказывания или
  пожертвования, иноагенты, протесты, религия); иначе n.
s skip — решить позже (метка не сохраняется), q — выйти."""


def load_labels(path: Path, articles: Sequence[Mapping[str, Any]]) -> dict[int, dict[str, Any]]:
    """The labels made so far, checked against the articles and the rubric; a propagated
    label must agree with the human label of the same text it names. A conflict is an
    error, never repaired."""
    if not path.exists():
        return {}
    by_id = {int(article["article_id"]): article for article in articles}
    labels: dict[int, dict[str, Any]] = {}
    for row in read_jsonl(path):
        article_id = int(row["article_id"])
        if article_id not in by_id:
            raise ValueError(f"label of an article not in the holdout: {article_id}")
        if article_id in labels:
            raise ValueError(f"{article_id} is labelled twice")
        if row["kind"] not in KINDS:
            raise ValueError(f"unknown kind {row['kind']!r} of {article_id}")
        if not isinstance(row["political"], bool):
            raise TypeError(f"political of {article_id} is not true/false")
        if row["political"] and row["kind"] != "case":
            raise ValueError(f"{article_id}: only a case is political")
        source = row.get("annotation_source")
        origin = row.get("propagated_from_article_id")
        if source == HUMAN and origin is not None:
            raise ValueError(f"{article_id}: a human label propagated from {origin}")
        if source not in (HUMAN, PROPAGATED):
            raise ValueError(f"{article_id}: unknown annotation_source {source!r}")
        labels[article_id] = {
            "article_id": article_id,
            "kind": row["kind"],
            "political": row["political"],
            "annotation_source": source,
        }
        if source == PROPAGATED:
            labels[article_id]["propagated_from_article_id"] = origin
    for article_id, label in labels.items():
        if label["annotation_source"] != PROPAGATED:
            continue
        origin = label["propagated_from_article_id"]
        if origin not in by_id:
            raise ValueError(f"{article_id}: propagated from {origin!r}, not in the sample")
        source = labels.get(origin)
        if source is None or source["annotation_source"] != HUMAN:
            raise ValueError(f"{article_id}: propagated from {origin}, not labelled by hand")
        if by_id[origin]["text_hash"] != by_id[article_id]["text_hash"]:
            raise ValueError(f"{article_id}: propagated from {origin} of another text")
        if (source["kind"], source["political"]) != (label["kind"], label["political"]):
            raise ValueError(f"{article_id}: label differs from {origin} it was propagated from")
    return labels


def save_labels(
    path: Path, articles: Sequence[Mapping[str, Any]], labels: Mapping[int, Mapping[str, Any]]
) -> None:
    """All labels, in the holdout's order, written whole or not at all."""
    body = "".join(
        json.dumps(dict(labels[int(article["article_id"])]), ensure_ascii=False) + "\n"
        for article in articles
        if int(article["article_id"]) in labels
    )
    write_atomically(path, body)


def pending(
    articles: Sequence[Mapping[str, Any]], labels: Mapping[int, Any]
) -> list[Mapping[str, Any]]:
    return [article for article in articles if int(article["article_id"]) not in labels]


def show(article: Mapping[str, Any], chars: int) -> str:
    body = article["text"].strip()
    cut = body[:chars] + (" …" if len(body) > chars else "")
    return (
        f"\n{'=' * 80}\n"
        f"article_id: {article['article_id']}\n"
        f"date:       {article['published_at']}\n"
        f"source:     {article['source']}\n"
        f"title:      {article['title']}\n"
        f"{'-' * 80}\n{cut}\n{'-' * 80}"
    )


def _ask(prompt: str, allowed: Sequence[str], ask: Callable[[str], str]) -> str:
    while True:
        answer = ask(prompt).strip().lower()
        if answer in allowed:
            return answer


def annotate(
    articles: Sequence[Mapping[str, Any]],
    labels_path: Path,
    *,
    ask: Callable[[str], str] | None = None,
    say: Callable[[str], None] | None = None,
    chars: int = CHARS,
    propagate: bool = False,
) -> int:
    """Label the articles not yet labelled until they end or `q`; how many were labelled
    by hand. With `propagate`, a hand label goes to the same texts too."""
    ask = ask or input
    say = say or print
    articles = ordered(articles)
    labels = load_labels(labels_path, articles)
    todo = pending(articles, labels)
    say(f"labelled {len(labels)} / {len(articles)}; {len(todo)} to go")
    done = 0
    for position, article in enumerate(todo, 1):
        if int(article["article_id"]) in labels:
            continue
        say(show(article, chars))
        while True:
            command = _ask(
                f"[{position}/{len(todo)}] c/a/p/f/n/o, s skip, q quit, ? rubric: ",
                [*COMMANDS, "s", "q", "?"],
                ask,
            )
            if command != "?":
                break
            say(RUBRIC)
        if command == "q":
            break
        if command == "s":
            continue
        kind = COMMANDS[command]
        political = kind == "case" and _ask("political? y/n: ", ["y", "n"], ask) == "y"
        article_id = int(article["article_id"])
        labels[article_id] = {
            "article_id": article_id,
            "kind": kind,
            "political": political,
            "annotation_source": HUMAN,
        }
        if propagate:
            for twin in articles:
                twin_id = int(twin["article_id"])
                if twin["text_hash"] == article["text_hash"] and twin_id not in labels:
                    labels[twin_id] = {
                        "article_id": twin_id,
                        "kind": kind,
                        "political": political,
                        "annotation_source": PROPAGATED,
                        "propagated_from_article_id": article_id,
                    }
        save_labels(labels_path, articles, labels)
        done += 1
        say(f"labelled {len(labels)} / {len(articles)}")
    say(f"labelled {len(labels)} / {len(articles)}")
    return done


POLITICAL_RULE = (
    "political — по рубрике junk-labels-v1, для ЛЮБОГО kind: y, если преследование "
    "по политическим мотивам; иначе n."
)


def _choose(
    ask: Callable[[str], str], say: Callable[[str], None], prompt: str
) -> tuple[str, bool] | str:
    """A kind and political, as `junk-labels-v1` asks them (political for every kind);
    or "s" / "q"."""
    while True:
        command = _ask(prompt, [*COMMANDS, "s", "q", "?"], ask)
        if command != "?":
            break
        say(RUBRIC)
        say(POLITICAL_RULE)
    if command in ("s", "q"):
        return command
    return COMMANDS[command], _ask("political? y/n: ", ["y", "n"], ask) == "y"


def _opinion(name: str, label: Mapping[str, Any] | None) -> str:
    if label is None:
        return f"{name}: no answer"
    return (
        f"{name}: {label['kind']}, political={str(label['political']).lower()} "
        f"({label.get('model_id')})\n   {label.get('explanation') or ''}"
    )


def _rows(path: Path) -> list[dict[str, Any]]:
    return read_jsonl(path) if path.exists() else []


def adjudicate(
    holdout_dir: Path,
    *,
    ask: Callable[[str], str] | None = None,
    say: Callable[[str], None] | None = None,
    chars: int = CHARS,
) -> int:
    """Decide the disagreements of A and B by hand; how many were decided."""
    ask = ask or input
    say = say or print
    rebuild(holdout_dir)
    articles = {int(a["article_id"]): a for a in read_jsonl(holdout_dir / "sample_articles.jsonl")}
    path = holdout_dir / "adjudications.jsonl"
    decided = {int(row["article_id"]): row for row in _rows(path)}
    todo = [
        row
        for row in _rows(holdout_dir / "annotation_disagreements.jsonl")
        if int(row["article_id"]) not in decided
    ]
    say(f"{len(decided)} decided, {len(todo)} to go")
    done = 0
    for position, row in enumerate(todo, 1):
        article_id = int(row["article_id"])
        say(show(articles[article_id], chars))
        say(f"differs: {row['differs']}\n{_opinion('A', row['a'])}\n{_opinion('B', row['b'])}")
        choice = _choose(
            ask, say, f"[{position}/{len(todo)}] c/a/p/f/n/o, s skip, q quit, ? rubric: "
        )
        if choice == "q":
            break
        if isinstance(choice, str):
            continue
        kind, political = choice
        decided[article_id] = {
            "article_id": article_id,
            "kind": kind,
            "political": political,
            "annotation_source": ADJUDICATION,
        }
        write_atomically(
            path,
            "".join(json.dumps(decided[key], ensure_ascii=False) + "\n" for key in sorted(decided)),
        )
        rebuild(holdout_dir)
        done += 1
    counts = rebuild(holdout_dir)
    say(f"{counts['adjudicated']} decided, {counts['remaining']} to go")
    return done


def audit(
    holdout_dir: Path,
    *,
    ask: Callable[[str], str] | None = None,
    say: Callable[[str], None] | None = None,
    chars: int = CHARS,
) -> int:
    """Check the audit's consensus labels by hand; how many were checked. A verdict is
    recorded beside the label, never in place of it."""
    ask = ask or input
    say = say or print
    ids = freeze_audit(holdout_dir)
    articles = {int(a["article_id"]): a for a in read_jsonl(holdout_dir / "sample_articles.jsonl")}
    labels = {
        int(row["article_id"]): row for row in read_jsonl(holdout_dir / "sample_labels.jsonl")
    }
    path = holdout_dir / "consensus_audit.jsonl"
    verdicts = {int(row["article_id"]): row for row in _rows(path)}
    todo = [article_id for article_id in ids if article_id not in verdicts]
    say(f"{len(verdicts)} / {len(ids)} checked")
    done = 0
    for position, article_id in enumerate(todo, 1):
        label = labels[article_id]
        if label["annotation_source"] != CONSENSUS:
            raise ValueError(f"{article_id} is not a consensus label")
        say(show(articles[article_id], chars))
        say(f"consensus: {label['kind']}, political={str(label['political']).lower()}")
        answer = _ask(
            f"[{position}/{len(todo)}] agree? y/n, s skip, q quit, ? rubric: ",
            ["y", "n", "s", "q", "?"],
            ask,
        )
        while answer == "?":
            say(RUBRIC)
            say(POLITICAL_RULE)
            answer = _ask("agree? y/n, s skip, q quit: ", ["y", "n", "s", "q", "?"], ask)
        if answer == "q":
            break
        if answer == "s":
            continue
        verdict: dict[str, Any] = {
            "article_id": article_id,
            "consensus_kind": label["kind"],
            "consensus_political": label["political"],
            "verdict": "agree" if answer == "y" else "disagree",
        }
        if answer == "n":
            choice = _choose(ask, say, "the right label: c/a/p/f/n/o, ? rubric: ")
            if isinstance(choice, str):
                break
            verdict["kind"], verdict["political"] = choice
        verdicts[article_id] = verdict
        write_atomically(
            path,
            "".join(
                json.dumps(verdicts[key], ensure_ascii=False) + "\n"
                for key in ids
                if key in verdicts
            ),
        )
        done += 1
    say(json.dumps(audit_agreement(list(verdicts.values()))))
    return done


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--dir", type=Path, default=HOLDOUT_DIR)
    parser.add_argument("--chars", type=int, default=CHARS)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--sample", action="store_true", help="label the fixed sample into sample_labels.jsonl"
    )
    mode.add_argument(
        "--disagreements", action="store_true", help="decide where the LLM annotators differ"
    )
    mode.add_argument("--audit", action="store_true", help="check 50 consensus labels by hand")
    args = parser.parse_args(argv)
    metadata = json.loads((args.dir / "metadata.json").read_text(encoding="utf-8"))
    if not metadata.get("frozen"):
        print("the holdout is not frozen; build it first")
        return 1
    if args.disagreements or args.audit:
        check_frozen(args.dir)
        try:
            (adjudicate if args.disagreements else audit)(args.dir, chars=args.chars)
        except (KeyboardInterrupt, EOFError):
            print("\nstopped; every decision given so far is saved")
        except ValueError as error:
            print(f"refused: {error}")
            return 1
        return 0
    if args.sample and any(
        (args.dir / name).exists() for name in ("labels_a.jsonl", "labels_b.jsonl")
    ):
        print("the sample is labelled by the LLM annotators; use --disagreements and --audit")
        return 1
    if args.sample:
        check_frozen(args.dir)
        articles_path, labels_path = (
            args.dir / "sample_articles.jsonl",
            args.dir / "sample_labels.jsonl",
        )
    else:
        articles_path, labels_path = args.dir / "articles.jsonl", args.dir / "labels.jsonl"
    try:
        annotate(read_jsonl(articles_path), labels_path, chars=args.chars, propagate=args.sample)
    except (KeyboardInterrupt, EOFError):
        print("\nstopped; every label given so far is saved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
