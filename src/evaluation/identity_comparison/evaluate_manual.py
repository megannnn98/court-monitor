"""Evaluate manual identity labels against stored decisions, without database access.

Accuracy describes this sampled disagreement set, not the full corpus. Only
same_person/different_person are identity ground truth; reviews are abstentions.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from evaluation.identity_comparison import labelling

IDENTITY_LABELS = ("same_person", "different_person")
PREDICTIONS = {
    "er_v2": {
        "one_person": "same_person",
        "different_persons": "different_person",
        "left_to_a_human": "abstain",
        "review": "abstain",
        "pending_review": "abstain",
    },
    "rules": {"one_entity": "same_person", "different_entities": "different_person"},
}
# The operator cycle replayed (`evaluation.identity_comparison.production`): the rules
# with the model's names, and with a person's decisions on top.
_REPLAYED = {
    "one_entity": "same_person",
    "different_entities": "different_person",
    # The sides share an entity and also hold others: a join in part.
    "partly_one_entity": "partial",
    # Kept apart and shown to the operator as a pair to decide. Like ER v2's REVIEW it
    # is a question put to a human; unlike it, the stored identity is «apart» meanwhile.
    "offered_to_operator": "abstain",
    # No entity was made of a side (the model said «not a person», or nothing placed
    # it). Nobody is asked: a result that is missing, not a review.
    "no_entity": "missing",
}
PREDICTIONS["rules_deepseek"] = _REPLAYED
PREDICTIONS["rules_deepseek_operator"] = _REPLAYED
# The same over the name parts the current normalizer stores.
PREDICTIONS["rules_replayed_normalizer_current"] = _REPLAYED
PREDICTIONS["rules_deepseek_normalizer_current"] = _REPLAYED
PREDICTIONS["rules_deepseek_operator_normalizer_current"] = _REPLAYED


def decision_of(pair: labelling.Pair, model: str) -> str | None:
    """A pair's own decision when the sample has one, else its case's."""
    found = pair.data.get("decisions", {}).get(model, pair.case["decisions"].get(model))
    return None if found is None else str(found)


def models_of(pairs: list[labelling.Pair]) -> list[str]:
    """The models the sample holds a decision of for every pair."""
    return [
        model
        for model in PREDICTIONS
        if pairs and all(decision_of(pair, model) is not None for pair in pairs)
    ]


def prediction(model: str, decision: str) -> str:
    try:
        return PREDICTIONS[model][decision.casefold()]
    except KeyError as error:
        raise ValueError(f"unknown {model} decision: {decision}") from error


def ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def outcome(label: str | None, predicted: str) -> str:
    if label not in IDENTITY_LABELS:
        return "non_evaluable"
    if predicted in ("abstain", "missing"):
        return predicted
    if predicted == "partial":
        # Any shared entity joins two different people; of one person it is half a join.
        return "false_merge" if label == "different_person" else "partial_merge"
    if label == predicted:
        return "correct"
    return "false_merge" if predicted == "same_person" else "false_split"


def model_metrics(rows: list[dict[str, Any]], model: str) -> dict[str, Any]:
    eligible = [row for row in rows if row["manual_label"] in IDENTITY_LABELS]
    counts = Counter(row[f"{model}_outcome"] for row in eligible)
    automatic = len(eligible) - counts["abstain"] - counts["missing"]
    return {
        "evaluable_pairs": len(eligible),
        "automatic_decisions": automatic,
        "abstains": counts["abstain"],
        # No entity was made of a side: nothing is stored and nobody is asked.
        "missing_entity": counts["missing"],
        # One person whose mentions the grouping joined only in part: not correct.
        "partial_merges": counts["partial_merge"],
        # What is stored for the pairs, a human's answer aside: a pair offered to the
        # operator is stored apart, so of one person it is a split until decided.
        "stored_correct": counts["correct"]
        + sum(
            row[f"{model}_outcome"] == "abstain"
            and row.get(f"{model}_decision") == "offered_to_operator"
            and row["manual_label"] == "different_person"
            for row in eligible
        ),
        "automatic_coverage": ratio(automatic, len(eligible)),
        "correct": counts["correct"],
        "wrong": counts["false_merge"] + counts["false_split"] + counts["partial_merge"],
        "automatic_accuracy": ratio(counts["correct"], automatic),
        "false_merges": counts["false_merge"],
        "false_splits": counts["false_split"],
        # Yield of correct automatic answers: abstentions remain a separate outcome,
        # not an error. This must not be called accuracy.
        "correct_fraction_of_evaluable": ratio(counts["correct"], len(eligible)),
        # Over every pair, the special labels too: decided, left to a human, nobody made.
        "all_pairs_automatic": sum(
            row[f"{model}_prediction"] not in ("abstain", "missing") for row in rows
        ),
        "all_pairs_abstains": sum(row[f"{model}_prediction"] == "abstain" for row in rows),
        "all_pairs_missing": sum(row[f"{model}_prediction"] == "missing" for row in rows),
        # A join is judged on the mentions the grouping placed. Of the correct pairs,
        # those with every mention of both sides in an entity; None where the sample
        # holds no counts for the model.
        "correct_with_every_mention_placed": _fully_placed(eligible, model),
    }


def _fully_placed(rows: list[dict[str, Any]], model: str) -> int | None:
    counted = [row for row in rows if row.get(f"{model}_replay") is not None]
    if not counted:
        return None
    return len(
        [
            row
            for row in counted
            if row[f"{model}_outcome"] == "correct"
            and row[f"{model}_replay"]["left_mentions_without_entity"] == 0
            and row[f"{model}_replay"]["right_mentions_without_entity"] == 0
        ]
    )


def stratum_metrics(rows: list[dict[str, Any]], models: list[str]) -> dict[str, Any]:
    counts = Counter(row["manual_label"] for row in rows)
    return {
        "pairs": len(rows),
        "evaluable": sum(counts[label] for label in IDENTITY_LABELS),
        "same_person": counts["same_person"],
        "different_person": counts["different_person"],
        **{
            model: {
                key: value
                for key, value in model_metrics(rows, model).items()
                if key
                in {
                    "correct",
                    "wrong",
                    "abstains",
                    "missing_entity",
                    "partial_merges",
                    "false_merges",
                    "false_splits",
                }
            }
            for model in models
        },
    }


def evaluate(sample: dict[str, Any], annotations: dict[str, Any]) -> dict[str, Any]:
    pairs = labelling.pairs_of(sample)
    known = {pair.identifier for pair in pairs}
    models = models_of(pairs)
    if set(annotations["annotations"]) - known:
        raise ValueError("annotations contain unknown pair IDs")
    rows: list[dict[str, Any]] = []
    for pair in pairs:
        entry = annotations["annotations"].get(pair.identifier, {})
        label = entry.get("label")
        if label is not None and label not in labelling.LABELS:
            raise ValueError("invalid label")
        flags = entry.get("flags", [])
        labelling.validate_flags(flags)
        # The source order is authoritative; supplied indices are checked, not used
        # to align the annotations or silently repair a mismatching source.
        if "index" in entry and entry["index"] != pair.number:
            raise ValueError(f"annotation index disagrees with source for {pair.identifier}")
        row: dict[str, Any] = {
            "index": pair.number,
            "pair_id": pair.identifier,
            "case_id": pair.case["case_id"],
            "stratum": pair.case["stratum"],
            "manual_label": label,
            "flags": list(flags),
            "comment": entry.get("comment"),
        }
        for model in models:
            decision = decision_of(pair, model)
            assert decision is not None
            predicted = prediction(model, decision)
            row[f"{model}_decision"] = decision
            row[f"{model}_prediction"] = predicted
            row[f"{model}_outcome"] = outcome(label, predicted)
            # The counts behind a replayed decision, where the sample has them.
            row[f"{model}_replay"] = pair.data.get("replay", {}).get(model)
        rows.append(row)
    labels = Counter(row["manual_label"] for row in rows)
    strata: dict[str, list[dict[str, Any]]] = defaultdict(list)
    exact_strata: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        strata[row["stratum"].split("/", 1)[0]].append(row)
        exact_strata[row["stratum"]].append(row)
    errors = {
        f"{model}_{plural}": [row for row in rows if row[f"{model}_outcome"] == singular]
        for model in models
        for singular, plural in (
            ("false_merge", "false_merges"),
            ("false_split", "false_splits"),
            ("abstain", "abstains"),
            ("missing", "missing_entities"),
            ("partial_merge", "partial_merges"),
        )
    }
    errors["special_or_unlabelled"] = [
        row for row in rows if row["manual_label"] not in IDENTITY_LABELS
    ]
    identity_errors = [
        row
        for row in rows
        if any(row[f"{model}_outcome"] in {"false_merge", "false_split"} for model in models)
    ]
    diagnostics = {flag: sum(flag in row["flags"] for row in rows) for flag in labelling.FLAGS} | {
        label: labels[label]
        for label in ("not_a_person", "mixed_identity_group", "invalid_mention")
    }
    return {
        "scope": "Manually labelled disagreement sample only; not corpus-wide accuracy.",
        "denominators": "Coverage uses evaluable pairs; accuracy uses automatic evaluable decisions. Abstentions are separate, not errors.",
        "overall": {
            "total_pairs": len(rows),
            "evaluable_identity_pairs": sum(labels[label] for label in IDENTITY_LABELS),
            **{label: labels[label] for label in labelling.LABELS},
            "unlabelled": labels[None],
        },
        "models": {model: model_metrics(rows, model) for model in models},
        "by_stratum": {
            key: stratum_metrics(value, models) for key, value in sorted(strata.items())
        },
        "by_exact_stratum": {
            key: stratum_metrics(value, models) for key, value in sorted(exact_strata.items())
        },
        "diagnostics": diagnostics,
        "error_categories": {
            "identity_errors_without_extraction_or_normalization_flags": [
                row["pair_id"]
                for row in identity_errors
                if not set(row["flags"]) & {"bad_span", "bad_normalization", "ner_error"}
            ],
            "identity_errors_with_extraction_flags": [
                row["pair_id"]
                for row in identity_errors
                if set(row["flags"]) & {"bad_span", "ner_error"}
            ],
            "identity_errors_with_normalization_flags": [
                row["pair_id"] for row in identity_errors if "bad_normalization" in row["flags"]
            ],
            "mixed_or_invalid_cases": [
                row["pair_id"]
                for row in rows
                if row["manual_label"] in {"mixed_identity_group", "invalid_mention"}
            ],
        },
        "errors": errors,
    }


def compare_reference(report: dict[str, Any], reference: dict[str, Any]) -> list[dict[str, Any]]:
    """Compare the supplied preliminary calculation without using it to score."""
    differences: list[dict[str, Any]] = []

    def compare(metric: str, expected: Any, actual: Any) -> None:
        if expected != actual:
            differences.append({"metric": metric, "expected": expected, "actual": actual})

    for label, count in reference["annotation_counts"].items():
        compare(label, count, report["overall"][label])
    preliminary = reference["identity_evaluation"]
    compare(
        "evaluable_pairs",
        preliminary["evaluable_pairs"],
        report["overall"]["evaluable_identity_pairs"],
    )
    mappings = {
        "er_v2": {
            "automatic_pairs": "automatic_decisions",
            "human_review_pairs": "abstains",
            "automatic_correct": "correct",
            "automatic_accuracy": "automatic_accuracy",
            "false_splits": "false_splits",
            "correct_over_all_evaluable_pairs": "correct_fraction_of_evaluable",
        },
        "rules": {
            "automatic_pairs": "automatic_decisions",
            "correct": "correct",
            "accuracy": "automatic_accuracy",
            "false_merges": "false_merges",
        },
    }
    for model, mapping in mappings.items():
        for old, new in mapping.items():
            compare(f"{model}.{new}", preliminary[model][old], report["models"][model][new])
    for group, expected in preliminary["by_stratum"].items():
        actual = report["by_stratum"][group]
        # The preliminary 'pairs' counts evaluable pairs; our 'pairs' includes specials.
        compare(f"{group}.evaluable", expected["pairs"], actual["evaluable"])
        for label in IDENTITY_LABELS:
            compare(f"{group}.{label}", expected[label], actual[label])
        compare(f"{group}.er_correct", expected["er_correct"], actual["er_v2"]["correct"])
        compare(f"{group}.er_abstain", expected["er_abstain"], actual["er_v2"]["abstains"])
        compare(f"{group}.rules_correct", expected["rules_correct"], actual["rules"]["correct"])
    for old, new in (
        ("rules_false_merge_pairs", "rules_false_merges"),
        ("er_false_split_pairs", "er_v2_false_splits"),
    ):
        expected = {row["pair_id"] for row in reference[old]}
        actual = {row["pair_id"] for row in report["errors"][new]}
        if expected != actual:
            differences.append(
                {
                    "metric": new,
                    "expected_only": sorted(expected - actual),
                    "actual_only": sorted(actual - expected),
                }
            )
    return differences


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=Path, required=True)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--reference", type=Path)
    parser.add_argument(
        "--out", type=Path, help="Full JSON report with error rows (no article text)"
    )
    args = parser.parse_args()
    try:
        if not args.annotations.exists():
            raise ValueError("annotations file does not exist")
        sample = json.loads(args.sample.read_text(encoding="utf-8"))
        annotations = labelling.load_annotations(args.annotations, labelling.pairs_of(sample))
        report = evaluate(sample, annotations)
        if args.reference:
            report["reference_differences"] = compare_reference(
                report, json.loads(args.reference.read_text(encoding="utf-8"))
            )
        if args.out:
            if args.out.resolve() in {
                p.resolve()
                for p in (args.sample, args.annotations, args.reference)
                if p is not None
            }:
                raise ValueError("output must not overwrite inputs")
            labelling.atomic_write(args.out, report)
        print(
            json.dumps(
                {
                    key: value
                    for key, value in report.items()
                    if key not in {"errors", "error_categories"}
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(2, f"Evaluation failed: {error}\n")


if __name__ == "__main__":
    main()
