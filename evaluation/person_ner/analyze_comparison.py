"""Reads the comparison dump and reports where the two person extractors disagree.

Metrics against the golden annotation are PRELIMINARY: that annotation is DRAFT and agent
produced, not human verified, so it is evidence for where to look, not a quality claim.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

SURNAME_ONLY = re.compile(r"^[А-ЯЁ][а-яё-]+$")
INITIALS = re.compile(r"[А-ЯЁ]\.")


def key(span: dict[str, Any]) -> tuple[str, int, int]:
    return span["article"], span["start_offset"], span["end_offset"]


def overlaps(a: tuple[str, int, int], b: tuple[str, int, int]) -> bool:
    return a[0] == b[0] and a[1] < b[2] and b[1] < a[2]


def context(text: str, start: int, end: int, width: int = 55) -> str:
    left = text[max(0, start - width) : start].replace("\n", " ")
    right = text[end : end + width].replace("\n", " ")
    return f"…{left}[[{text[start:end]}]]{right}…"


def classify(surface: str) -> str:
    if INITIALS.search(surface):
        return "initials"
    words = surface.split()
    if len(words) == 1:
        return "single_word" if SURNAME_ONLY.match(surface) else "single_other"
    return f"{len(words)}_words"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=Path("var/person_ner/comparison.json"))
    parser.add_argument("--examples", type=int, default=25)
    args = parser.parse_args()

    data = json.loads(args.report.read_text(encoding="utf-8"))
    texts: dict[str, str] = data["texts"]
    golden_by_article: dict[str, list[dict[str, Any]]] = data["golden"]

    rule = [s for s in data["spans"] if s["extractor"] == "rule_based"]
    model = [s for s in data["spans"] if s["extractor"] == "gliner"]
    rule_keys = {key(s) for s in rule}
    model_keys = {key(s) for s in model}

    exact = rule_keys & model_keys
    rule_only = [s for s in rule if key(s) not in model_keys]
    model_only = [s for s in model if key(s) not in rule_keys]

    boundary = [
        (r, m)
        for r in rule_only
        for m in model_only
        if overlaps(key(r), key(m)) and key(r) != key(m)
    ]
    boundary_rule_keys = {key(r) for r, _ in boundary}
    boundary_model_keys = {key(m) for _, m in boundary}
    rule_unique = [s for s in rule_only if key(s) not in boundary_rule_keys]
    model_unique = [s for s in model_only if key(s) not in boundary_model_keys]

    golden_keys = {
        (article, g["start"], g["end"])
        for article, spans in golden_by_article.items()
        for g in spans
    }

    def against_golden(spans: list[dict[str, Any]]) -> dict[str, Any]:
        keys = {key(s) for s in spans}
        hit = keys & golden_keys
        overlap_hit = {g for g in golden_keys if any(overlaps(g, k) for k in keys)}
        return {
            "spans": len(spans),
            "exact_tp": len(hit),
            "exact_precision": round(len(hit) / len(keys), 4) if keys else 0.0,
            "exact_recall": round(len(hit) / len(golden_keys), 4) if golden_keys else 0.0,
            "overlap_recall": round(len(overlap_hit) / len(golden_keys), 4) if golden_keys else 0.0,
        }

    summary = {
        "articles": data["articles"],
        "chars": data["chars"],
        "golden_person_mentions": len(golden_keys),
        "golden_status": data["golden_status"],
        "rule_based_spans": len(rule),
        "gliner_spans": len(model),
        "exact_agreement": len(exact),
        "disagreements": len(rule_only) + len(model_only),
        "boundary_disagreements": len(boundary),
        "rule_only": len(rule_unique),
        "gliner_only": len(model_unique),
        "offset_violations": len(data["offset_violations"]),
        "vs_golden_PRELIMINARY": {
            "rule_based": against_golden(rule),
            "gliner": against_golden(model),
        },
        "shape_rule_only": Counter(classify(s["surface_text"]) for s in rule_unique).most_common(),
        "shape_gliner_only": Counter(
            classify(s["surface_text"]) for s in model_unique
        ).most_common(),
    }

    print(json.dumps(summary, ensure_ascii=False, indent=2))

    print("\n=== FOUND ONLY BY THE RULE EXTRACTOR (candidate false positives) ===")
    for span in sorted(rule_unique, key=lambda s: -len(s["surface_text"]))[: args.examples]:
        print(
            f"  {span['surface_text']!r}\n    {context(texts[span['article']], span['start_offset'], span['end_offset'])}"
        )

    print("\n=== FOUND ONLY BY GLINER (candidate rule-extractor misses) ===")
    for span in sorted(model_unique, key=lambda s: -s["confidence"])[: args.examples]:
        print(
            f"  {span['surface_text']!r} conf={span['confidence']}\n    {context(texts[span['article']], span['start_offset'], span['end_offset'])}"
        )

    print("\n=== BOUNDARY DISAGREEMENTS ===")
    for rule_span, model_span in boundary[: args.examples]:
        print(f"  rule={rule_span['surface_text']!r}  gliner={model_span['surface_text']!r}")

    out = args.report.with_name("disagreements_DRAFT.json")
    out.write_text(
        json.dumps(
            {
                "status": "DRAFT — awaiting human verification",
                "summary": summary,
                "rule_only": rule_unique,
                "gliner_only": model_unique,
                "boundary": [{"rule": r, "gliner": m} for r, m in boundary],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\ndisagreement dataset: {out}")


if __name__ == "__main__":
    main()
