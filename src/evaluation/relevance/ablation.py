"""Preprocessing ablation: the same baseline over four readings of the text.

Every variant has the same split, TF-IDF, LogisticRegression, threshold rule and recall
target; only the preprocessing differs. The variant is chosen on validation alone: of those
whose threshold reaches the recall target, the most precise, then the higher PR-AUC, then
the simpler (earlier in `PREPROCESSORS`). A variant short of the target is never chosen
while another reaches it.

The test is scored only after the choice is made, for the chosen variant and for `raw`
to compare their errors. It has been looked at by earlier experiments, so it is no longer a
fully independent holdout; the report says so.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from evaluation.relevance.analysis import model_features
from evaluation.relevance.baseline import (
    Article,
    Fitted,
    build_model,
    calculate_metrics,
    fit_and_choose_threshold,
    probabilities_of,
)
from evaluation.relevance.preprocessing import PREPROCESSORS, lemmatize

TRACKED_FEATURES = (
    "max",
    "https",
    "подписывайтесь",
    "августа",
    "яблока",
    "яблоко",
    "дело",
    "делу",
    "по делу",
)
COLLAPSED_LEMMAS = ("дело", "яблоко", "август")


@dataclass(frozen=True)
class Variant:
    name: str
    fitted: Fitted
    validation: dict[str, Any]


def fit_variants(
    train_rows: Sequence[Article],
    preprocessors: Mapping[str, Callable[[str], str]] = PREPROCESSORS,
    model_factory: Callable[[], Any] = build_model,
) -> dict[str, Variant]:
    """Each preprocessing fitted and thresholded on the train file alone."""
    variants: dict[str, Variant] = {}
    for name, preprocess in preprocessors.items():
        fitted = fit_and_choose_threshold(
            train_rows, model_factory=model_factory, preprocess=preprocess
        )
        variants[name] = Variant(
            name=name,
            fitted=fitted,
            validation=calculate_metrics(
                [row.label for row in fitted.validation],
                fitted.validation_probabilities,
                fitted.threshold.threshold,
            ),
        )
    splits = {
        (
            tuple(row.article_id for row in variant.fitted.train),
            tuple(row.article_id for row in variant.fitted.validation),
        )
        for variant in variants.values()
    }
    if len(splits) > 1:
        raise RuntimeError("variants were split differently; they cannot be compared")
    return variants


def select_variant(validation: Mapping[str, Mapping[str, Any]]) -> tuple[str, bool]:
    """The chosen variant's name, and whether it reached the recall target. `validation`
    holds, per variant in order of simplicity, `target_met`, `precision`, `recall` and
    `average_precision` on validation — nothing else is there to choose by."""
    if not validation:
        raise ValueError("no variants")
    order = {name: index for index, name in enumerate(validation)}
    reaching = [name for name, entry in validation.items() if entry["target_met"]]
    if reaching:
        best = max(
            reaching,
            key=lambda name: (
                validation[name]["precision"],
                validation[name]["average_precision"],
                -order[name],
            ),
        )
        return best, True
    best = max(
        validation,
        key=lambda name: (
            validation[name]["recall"],
            validation[name]["precision"],
            -order[name],
        ),
    )
    return best, False


def error_diff(
    rows: Sequence[Article],
    base_probabilities: Sequence[float],
    base_threshold: float,
    new_probabilities: Sequence[float],
    new_threshold: float,
) -> dict[str, list[int]]:
    """Article ids of the errors the new variant fixes and those it brings, by kind."""
    if not len(rows) == len(base_probabilities) == len(new_probabilities):
        raise ValueError("articles and probabilities differ in number")

    def errors(probabilities: Sequence[float], threshold: float) -> tuple[set[int], set[int]]:
        fn = {
            row.article_id
            for row, p in zip(rows, probabilities, strict=True)
            if row.label and p < threshold
        }
        fp = {
            row.article_id
            for row, p in zip(rows, probabilities, strict=True)
            if not row.label and p >= threshold
        }
        return fn, fp

    base_fn, base_fp = errors(base_probabilities, base_threshold)
    new_fn, new_fp = errors(new_probabilities, new_threshold)
    return {
        "fn_fixed": sorted(base_fn - new_fn),
        "fn_introduced": sorted(new_fn - base_fn),
        "fp_fixed": sorted(base_fp - new_fp),
        "fp_introduced": sorted(new_fp - base_fp),
    }


def _vocabulary(model: Any) -> dict[str, int]:
    return dict(model.named_steps["tfidf"].vocabulary_)


def tracked_weights(model: Any, words: Sequence[str] = TRACKED_FEATURES) -> dict[str, float | None]:
    """The weight of each word in the model, None when it is not a feature at all."""
    vocabulary = _vocabulary(model)
    weights = model.named_steps["classifier"].coef_[0]
    return {
        word: (float(weights[vocabulary[word]]) if word in vocabulary else None) for word in words
    }


def collapsed_forms(
    raw_model: Any, lemma_model: Any, lemmas: Sequence[str] = COLLAPSED_LEMMAS
) -> dict[str, dict[str, Any]]:
    """For each lemma: the word forms the raw model holds as separate features, and which
    of them the lemmatized model still holds."""
    raw_words = [word for word in _vocabulary(raw_model) if " " not in word]
    lemma_vocabulary = _vocabulary(lemma_model)
    result: dict[str, dict[str, Any]] = {}
    for lemma in lemmas:
        forms = sorted(word for word in raw_words if lemmatize(word) == lemma)
        result[lemma] = {
            "raw_forms": forms,
            "forms_left_after_lemma": sorted(form for form in forms if form in lemma_vocabulary),
            "lemma_is_feature": lemma in lemma_vocabulary,
        }
    return result


def run_ablation(
    train_rows: Sequence[Article],
    test_rows: Sequence[Article],
    preprocessors: Mapping[str, Callable[[str], str]] = PREPROCESSORS,
    model_factory: Callable[[], Any] = build_model,
) -> dict[str, Any]:
    if "raw" not in preprocessors:
        raise ValueError("the raw baseline is the control and must be among the variants")
    variants = fit_variants(train_rows, preprocessors, model_factory)
    selected, target_met = select_variant(
        {
            name: {
                "target_met": variant.fitted.threshold.target_met,
                "precision": variant.validation["precision"],
                "recall": variant.validation["recall"],
                "average_precision": variant.validation["average_precision"],
            }
            for name, variant in variants.items()
        }
    )

    # The choice is made. From here on the test is only looked at.
    if not test_rows:
        raise ValueError("no test articles")
    test_labels = [row.label for row in test_rows]
    scored: dict[str, tuple[list[float], float]] = {}
    for name in dict.fromkeys(("raw", selected)):
        variant = variants[name]
        scored[name] = (
            probabilities_of(variant.fitted.model, test_rows, preprocessors[name]),
            variant.fitted.threshold.threshold,
        )
    raw_validation = variants["raw"].fitted
    chosen = variants[selected].fitted
    report: dict[str, Any] = {
        name: {
            "threshold": asdict(variant.fitted.threshold),
            "validation": variant.validation,
            "top_features": model_features(variant.fitted.model),
            "tracked_features": tracked_weights(variant.fitted.model),
        }
        for name, variant in variants.items()
    }
    for name, (probabilities, threshold) in scored.items():
        report[name]["test"] = calculate_metrics(test_labels, probabilities, threshold)
    report["selected"] = selected
    report["selected_target_met"] = target_met
    report["selection_source"] = "validation"
    report["test_previously_observed"] = True
    report["split"] = {
        "train": len(chosen.train),
        "validation": len(chosen.validation),
        "test": len(test_rows),
    }
    report["errors_vs_raw"] = {
        "validation": error_diff(
            chosen.validation,
            raw_validation.validation_probabilities,
            raw_validation.threshold.threshold,
            chosen.validation_probabilities,
            chosen.threshold.threshold,
        ),
        "test": error_diff(test_rows, *scored["raw"], *scored[selected]),
    }
    if "lemma" in variants:
        report["collapsed_forms"] = collapsed_forms(
            variants["raw"].fitted.model, variants["lemma"].fitted.model
        )
    return report
