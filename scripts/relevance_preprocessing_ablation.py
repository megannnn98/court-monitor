"""Compare raw / cleanup / lemma / cleanup + lemma preprocessing of the relevance baseline.
The choice is made on validation; the test is shown afterwards. The logic lives in
`evaluation.relevance.ablation`.

    PYTHONPATH=src python scripts/relevance_preprocessing_ablation.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from evaluation.relevance.ablation import run_ablation
from evaluation.relevance.baseline import load_dataset
from evaluation.relevance.preprocessing import PREPROCESSORS

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data/ml/relevance"
RESULTS_DIR = DATA_DIR / "results"


def _metrics_row(name: str, entry: dict[str, Any], part: str) -> str:
    m = entry[part]
    met = "" if entry["threshold"]["target_met"] else "  recall < 0.90!"
    return (
        f"  {name:<14} {m['average_precision']:>6.3f} {m['roc_auc']:>6.3f} "
        f"{m['precision']:>6.3f} {m['recall']:>6.3f} {m['f1']:>6.3f} "
        f"{entry['threshold']['threshold']:>7.4f} {m['fp']:>4} {m['fn']:>4}{met}"
    )


def _print(report: dict[str, Any]) -> None:
    head = f"  {'variant':<14} {'PR-AUC':>6} {'ROC':>6} {'prec':>6} {'rec':>6} {'F1':>6}"
    head += f" {'thr':>7} {'FP':>4} {'FN':>4}"
    print(f"split: {report['split']}\n\nvalidation (selection source)\n{head}")
    for name in PREPROCESSORS:
        print(_metrics_row(name, report[name], "validation"))
    selected = report["selected"]
    print(f"\nselected: {selected} (target met: {report['selected_target_met']})")
    print("\ntest (seen by earlier experiments; not an independent holdout)\n" + head)
    for name in dict.fromkeys(("raw", selected)):
        print(_metrics_row(name, report[name], "test"))
    for part, diff in report["errors_vs_raw"].items():
        print(f"\nerrors of {selected} vs raw on {part}:")
        for key, ids in diff.items():
            print(f"  {key:<14} {len(ids):>3} {ids if len(ids) <= 20 else ids[:20] + ['…']}")
    print("\ntracked features (weight; — = not a feature)")
    words = list(report["raw"]["tracked_features"])
    print(f"  {'':<14}" + "".join(f"{word:>15}" for word in words))
    for name in PREPROCESSORS:
        tracked = report[name]["tracked_features"]
        cells = "".join(
            f"{'—' if tracked[word] is None else format(tracked[word], '+.2f'):>15}"
            for word in words
        )
        print(f"  {name:<14}{cells}")
    for lemma, forms in report.get("collapsed_forms", {}).items():
        print(
            f"\ncollapsed «{lemma}»: raw forms {forms['raw_forms']} → "
            f"left after lemma {forms['forms_left_after_lemma']}, "
            f"lemma a feature: {forms['lemma_is_feature']}"
        )
    for name in PREPROCESSORS:
        features = report[name]["top_features"]
        for side in ("case", "not_case"):
            top = ", ".join(f"{p['feature']} {p['weight']:+.2f}" for p in features[side][:15])
            print(f"\n{name} → {side}: {top}")


def main() -> None:
    report = run_ablation(
        load_dataset(DATA_DIR / "train.jsonl"), load_dataset(DATA_DIR / "test.jsonl")
    )
    _print(report)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / "preprocessing_ablation.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nresults: {path}")


if __name__ == "__main__":
    main()
