"""The disagreement sample made ready for a person to label, with the text around
every mention.

    python -m evaluation.identity_comparison.annotation \\
        --real-url postgresql+psycopg://…@127.0.0.1:5434/junk_eval \\
        --out var/identity_comparison

Nothing is labelled here. A case of the sample becomes one or more pairs of sides, and
a person says of each pair whether its two sides are one human: `same_person`,
`different_person` or `insufficient_evidence`. A side is a group one of the models made
— an ER v2 person, an entity of the rules, or a single mention — shown by a few of its
mentions with a window of the article around each. The file holds names and text of
the real database and belongs under the ignored `var/`. The database is only read.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, make_url, text

from evaluation.identity_comparison import real

LABELS = ("same_person", "different_person", "insufficient_evidence")
# Characters of the article kept on each side of a mention.
WINDOW = 300
# Mentions that show one side, from as many articles as it has.
MENTIONS_PER_SIDE = 3
# A case of many groups is asked pair by pair; past this many, the rest is left out.
MAX_PAIRS = 6
ER_CANDIDATES = 3

_CONTEXT = text(
    """
    SELECT m.id, m.surface_text, m.normalized_text, m.normalized_data,
           m.start_offset, m.end_offset, m.person_id,
           a.id, a.title, a.published_at, a.text, s.name, sd.canonical_url
    FROM entity_mentions m
    JOIN article_extraction_runs r ON r.id = m.extraction_run_id
    JOIN parsed_articles a ON a.id = r.article_id
    JOIN source_documents sd ON sd.id = a.document_id
    JOIN sources s ON s.id = sd.source_id
    WHERE m.id = ANY(:ids)
    """
)
_EVENTS = text(
    """
    SELECT em.mention_id, e.event_type, em.role, e.event_date
    FROM event_entity_mentions em
    JOIN extracted_events e ON e.id = em.event_id
    WHERE em.mention_id = ANY(:ids)
    ORDER BY em.mention_id, e.id
    """
)
_DECISIONS = text(
    """
    SELECT DISTINCT ON (mention_id) mention_id, action, status, reasons, resolution_score,
           selected_person_id, candidates
    FROM person_resolution_decisions
    WHERE mention_id = ANY(:ids)
    ORDER BY mention_id, id DESC
    """
)


@dataclass(frozen=True)
class MentionContext:
    mention_id: int
    surface: str
    normalized: str
    normalized_data: Mapping[str, Any]
    start: int
    end: int
    person_id: int | None
    article_id: int
    title: str | None
    published_at: str | None
    article_text: str
    source: str | None
    url: str | None
    events: tuple[dict[str, Any], ...] = ()
    er_decision: Mapping[str, Any] | None = None


@dataclass
class Side:
    """One group to compare: who made it, its technical id, and the mentions shown."""

    kind: str
    group: dict[str, Any]
    mention_ids: list[int] = field(default_factory=list)


def case_id(stratum: str, mention_ids: Sequence[int]) -> str:
    """The same for the same mentions of the same kind of case, whatever the run."""
    digest = hashlib.sha256(
        f"{stratum}|{','.join(map(str, sorted(mention_ids)))}".encode()
    ).hexdigest()
    return f"case-{digest[:12]}"


def snippet(article_text: str, start: int, end: int, window: int = WINDOW) -> dict[str, Any]:
    """The text around [start, end), cut at spaces, and where the mention is in it."""
    left = max(start - window, 0)
    right = min(end + window, len(article_text))
    if left > 0 and (space := article_text.find(" ", left, start)) != -1:
        left = space + 1
    if right < len(article_text) and (space := article_text.rfind(" ", end, right)) != -1:
        right = space
    return {
        "text": article_text[left:right],
        "mention_start": start - left,
        "mention_end": end - left,
        "cut_before": left > 0,
        "cut_after": right < len(article_text),
    }


def shown_mentions(mention_ids: Sequence[int], contexts: Mapping[int, MentionContext]) -> list[int]:
    """A few mentions of a side: one from each article first, the earliest ids."""
    known = sorted(mention_id for mention_id in mention_ids if mention_id in contexts)
    first_of_article: dict[int, int] = {}
    for mention_id in known:
        first_of_article.setdefault(contexts[mention_id].article_id, mention_id)
    chosen = sorted(first_of_article.values())[:MENTIONS_PER_SIDE]
    rest = [mention_id for mention_id in known if mention_id not in chosen]
    return sorted(chosen + rest[: MENTIONS_PER_SIDE - len(chosen)])


def sides_of(case: Mapping[str, Any]) -> tuple[list[Side], dict[str, str]]:
    """The groups of a case and what each model said of them."""
    stratum = str(case["stratum"])
    if stratum.startswith("rules_join_er_splits"):
        return [
            Side(
                "er_person",
                {"person_id": person["person_id"], "canonical_name": person["canonical_name"]},
                list(person["mention_ids"]),
            )
            for person in case["persons"]
        ], {"er_v2": "different_persons", "rules": "one_entity"}
    if stratum == "er_joins_rules_split":
        return [
            Side("rules_entity", {"entity": entity["entity"]}, list(entity["mention_ids"]))
            for entity in case["entities"]
        ], {"er_v2": "one_person", "rules": "different_entities"}
    if stratum == "er_review_rules_place":
        return [
            Side("mention", {"mention_id": case["mention_id"]}, [case["mention_id"]]),
            Side("rules_entity", {"entity": case["entity"]}, list(case["other_mention_ids"])),
        ], {"er_v2": "left_to_a_human", "rules": "one_entity"}
    raise ValueError(f"unknown stratum: {stratum}")


def needed_mentions(sample: Sequence[Mapping[str, Any]]) -> set[int]:
    return {
        mention_id
        for case in sample
        for side in sides_of(case)[0]
        for mention_id in side.mention_ids
    }


def _mention_entry(context: MentionContext) -> dict[str, Any]:
    return {
        "mention_id": context.mention_id,
        "article_id": context.article_id,
        "surface": context.surface,
        "normalized": context.normalized,
        "normalized_data": dict(context.normalized_data),
        "er_person_id": context.person_id,
        "er_decision": dict(context.er_decision) if context.er_decision else None,
        "article": {
            "title": context.title,
            "published_at": context.published_at,
            "source": context.source,
            "url": context.url,
        },
        "events": list(context.events),
        "context": snippet(context.article_text, context.start, context.end),
    }


def annotate(
    sample: Sequence[Mapping[str, Any]],
    contexts: Mapping[int, MentionContext],
    entity_of: Mapping[int, str],
) -> list[dict[str, Any]]:
    """Every case as pairs of sides to label. A case whose sides cannot be shown gets
    no pair and says why in `not_ready`."""
    cases: list[dict[str, Any]] = []
    for case in sample:
        sides, decisions = sides_of(case)
        shown = [shown_mentions(side.mention_ids, contexts) for side in sides]
        entries = [
            {
                "side": chr(ord("a") + index),
                "kind": side.kind,
                "group": side.group,
                "mentions_in_group": len(side.mention_ids),
                "mentions": [
                    {
                        **_mention_entry(contexts[mention_id]),
                        "rules_entity": entity_of.get(mention_id),
                    }
                    for mention_id in mention_ids
                ],
            }
            for index, (side, mention_ids) in enumerate(zip(sides, shown, strict=True))
        ]
        ready = [entry for entry in entries if entry["mentions"]]
        identifier = case_id(
            str(case["stratum"]), [mention_id for side in sides for mention_id in side.mention_ids]
        )
        pairs = [
            {
                "pair_id": f"{identifier}:{left['side']}-{right['side']}",
                "sides": [left["side"], right["side"]],
                "question": "Are the two sides one human?",
                "annotation": {"label": None, "comment": None},
            }
            for left, right in list(combinations(ready, 2))[:MAX_PAIRS]
        ]
        not_ready = None
        if len(sides) < 2 or any(not side.mention_ids for side in sides):
            not_ready = "a side has no mention: the rules put this mention in an entity alone"
        elif len(ready) < len(entries):
            not_ready = "no stored context for the mentions of a side"
        cases.append(
            {
                "case_id": identifier,
                "stratum": case["stratum"],
                "decisions": decisions,
                "sides": entries,
                "pairs": pairs if not_ready is None else [],
                "pairs_left_out": max(len(list(combinations(ready, 2))) - MAX_PAIRS, 0),
                "not_ready": not_ready,
            }
        )
    return cases


def load_contexts(engine: Engine, mention_ids: Sequence[int]) -> dict[int, MentionContext]:
    ids = sorted(mention_ids)
    events: dict[int, list[dict[str, Any]]] = defaultdict(list)
    decisions: dict[int, dict[str, Any]] = {}
    with engine.connect() as connection:
        for mention_id, event_type, role, event_date in connection.execute(_EVENTS, {"ids": ids}):
            events[mention_id].append(
                {
                    "event_type": event_type,
                    "role": role,
                    "event_date": event_date.isoformat() if event_date else None,
                }
            )
        for mention_id, action, status, reasons, score, selected, candidates in connection.execute(
            _DECISIONS, {"ids": ids}
        ):
            decisions[mention_id] = {
                "action": action,
                "status": status,
                "reasons": list(reasons or []),
                "resolution_score": score,
                "selected_person_id": selected,
                "candidates": [
                    {
                        "person_id": item.get("candidate", {}).get("person_id"),
                        "canonical_name": item.get("candidate", {}).get("canonical_name"),
                    }
                    for item in (candidates or [])[:ER_CANDIDATES]
                ],
            }
        return {
            row[0]: MentionContext(
                mention_id=row[0],
                surface=row[1],
                normalized=row[2],
                normalized_data=row[3] or {},
                start=row[4],
                end=row[5],
                person_id=row[6],
                article_id=row[7],
                title=row[8],
                published_at=row[9].isoformat() if row[9] else None,
                article_text=row[10] or "",
                source=row[11],
                url=row[12],
                events=tuple(events.get(row[0], ())),
                er_decision=decisions.get(row[0]),
            )
            for row in connection.execute(_CONTEXT, {"ids": ids})
        }


def summary(cases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    lengths = [
        len(mention["context"]["text"])
        for case in cases
        for side in case["sides"]
        for mention in side["mentions"]
    ]
    per_case = [
        sum(
            len(mention["context"]["text"])
            for side in case["sides"]
            for mention in side["mentions"]
        )
        for case in cases
    ]
    not_ready: dict[str, int] = defaultdict(int)
    for case in cases:
        if case["not_ready"]:
            not_ready[case["not_ready"]] += 1
    return {
        "cases": len(cases),
        "cases_ready": sum(case["not_ready"] is None for case in cases),
        "cases_not_ready": dict(not_ready),
        "pairs_to_label": sum(len(case["pairs"]) for case in cases),
        "pairs_left_out": sum(case["pairs_left_out"] for case in cases),
        "mentions_shown": len(lengths),
        "context_chars_per_mention_mean": round(sum(lengths) / len(lengths)) if lengths else 0,
        "context_chars_per_mention_max": max(lengths, default=0),
        "context_chars_per_case_mean": round(sum(per_case) / len(per_case)) if per_case else 0,
        "context_chars_per_case_max": max(per_case, default=0),
    }


def build(real_url: str) -> list[dict[str, Any]]:
    engine = real.read_only_engine(real_url)
    try:
        rows, _ = real.load_rows(engine)
        _, entity_of = real.compare(rows)
        _, sample = real.disagreement_sample(rows, entity_of, real.person_names(engine))
        contexts = load_contexts(engine, sorted(needed_mentions(sample)))
        return annotate(sample, contexts, entity_of)
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    parser.add_argument("--real-url", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    cases = build(args.real_url)
    args.out.mkdir(parents=True, exist_ok=True)
    document = {
        "database": make_url(args.real_url).database,
        "labels": list(LABELS),
        "how_to_label": (
            "For each pair set annotation.label to one of `labels`; "
            "`insufficient_evidence` when the text does not tell who the person is."
        ),
        "cases": cases,
    }
    (args.out / "disagreement_annotation.json").write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary(cases), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
