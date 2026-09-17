"""A proposed reading of every extractor disagreement, for a human to confirm or reject.

This does NOT produce verified annotation. It is an agent's adjudication, and the corpus
it judges was itself drafted by an agent; calling the result VERIFIED would be exactly the
circularity the DRAFT status exists to prevent. What it does is cheaper for a reviewer:
each case arrives with a verdict, a reason and the sentence it sits in, so the work is
confirming or rejecting rather than judging from scratch.

    uv run python evaluation/person_ner/adjudicate_disagreements.py
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

COMPARISON = Path("var/person_ner/comparison.json")
DISAGREEMENTS = Path("var/person_ner/disagreements_DRAFT.json")

# Judgments that are checkable facts about the language, not opinions: a slogan, a
# country, an organization and a common noun are not names of people, whoever extracted
# them. Keyed by the exact surface form the extractors reported.
NOT_A_PERSON: dict[str, str] = {
    "Слава Украине": "slogan, quoted as the text of a leaflet or a post",
    "Украине": "country, in an inflected form",
    "Команда Навального": "organization: the name of a project and its channel",
    "Харп Ямало-Ненецкого": "settlement and the region it is in",
    "ЦИК": "the Central Election Commission, an institution",
    "крымчанина": "common noun for a resident of Crimea, not a name",
    "калужанин": "common noun for a resident of Kaluga, not a name",
    "пидор": "a slur quoted in direct speech, not a name",
}

# Genuinely arguable: the reviewer decides, and the reason says what the argument is.
NEEDS_A_HUMAN: dict[str, str] = {
    "Алан Грант": (
        "the title of a Telegram channel, «Алан Грант — наука с публицистикой». It reads "
        "like a pen name, so whether the channel's author is a person mention here is a "
        "judgement about the article, not about the words"
    ),
    "Калужанин": (
        "capitalised mid-sentence: either the common noun for a Kaluga resident at the "
        "start of a sentence, or the surname Калужанин. Only the sentence tells them apart"
    ),
}


def context(text: str, start: int, end: int, width: int = 70) -> str:
    left = text[max(0, start - width) : start].replace("\n", " ")
    right = text[end : end + width].replace("\n", " ")
    return f"…{left}[[{text[start:end]}]]{right}…"


def verdict(surface: str) -> tuple[str, str]:
    if surface in NOT_A_PERSON:
        return "NOT_A_PERSON", NOT_A_PERSON[surface]
    if surface in NEEDS_A_HUMAN:
        return "NEEDS_A_HUMAN", NEEDS_A_HUMAN[surface]
    return "PERSON", "a personal name in an inflected or bare form"


def adjudicate(cases: list[dict[str, Any]], texts: dict[str, str], found_by: str) -> list[dict]:
    judged: list[dict[str, Any]] = []
    for case in cases:
        label, reason = verdict(case["surface_text"])
        judged.append(
            {
                "found_by": found_by,
                "surface_text": case["surface_text"],
                "article": case["article"],
                "start_offset": case["start_offset"],
                "end_offset": case["end_offset"],
                "verdict": label,
                "reason": reason,
                # What the verdict means for the extractor that reported it alone.
                "implication": (
                    "false positive of " + found_by
                    if label == "NOT_A_PERSON"
                    else "missed by the other extractor"
                    if label == "PERSON"
                    else "undecided"
                ),
                "context": context(
                    texts[case["article"]], case["start_offset"], case["end_offset"]
                ),
            }
        )
    return judged


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("var/person_ner"))
    args = parser.parse_args()

    texts = json.loads(COMPARISON.read_text(encoding="utf-8"))["texts"]
    data = json.loads(DISAGREEMENTS.read_text(encoding="utf-8"))

    judged = adjudicate(data["rule_only"], texts, "rule_based") + adjudicate(
        data["gliner_only"], texts, "gliner"
    )

    by_extractor: dict[str, Counter[str]] = {}
    for case in judged:
        by_extractor.setdefault(case["found_by"], Counter())[case["verdict"]] += 1

    print(f"{'found only by':14} {'PERSON':>8} {'NOT_A_PERSON':>14} {'NEEDS_A_HUMAN':>15}")
    for found_by, counts in sorted(by_extractor.items()):
        print(
            f"{found_by:14} {counts['PERSON']:8d} {counts['NOT_A_PERSON']:14d} "
            f"{counts['NEEDS_A_HUMAN']:15d}"
        )

    print("\n=== false positives, by extractor ===")
    for case in judged:
        if case["verdict"] == "NOT_A_PERSON":
            print(f"  [{case['found_by']}] {case['surface_text']!r} — {case['reason']}")

    print("\n=== left for a human ===")
    for case in judged:
        if case["verdict"] == "NEEDS_A_HUMAN":
            print(f"  [{case['found_by']}] {case['surface_text']!r}\n      {case['reason']}")
            print(f"      {case['context']}")

    out = args.output / "adjudication_DRAFT.json"
    out.write_text(
        json.dumps(
            {
                "status": "DRAFT — agent adjudication, NOT verified annotation",
                "how_to_use": (
                    "Confirm or reject each verdict. Only a human pass turns this into "
                    "ground truth; until then every metric derived from it stays PRELIMINARY."
                ),
                "counts": {k: dict(v) for k, v in by_extractor.items()},
                "cases": judged,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nadjudication: {out}")


if __name__ == "__main__":
    main()
