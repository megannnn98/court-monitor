"""Does an embedding screen keep the articles today's extraction wrongly purges?

The labelled sample (`corpus.py`, `labels.py`) is split by date: the earlier part
calibrates (it gives the examples and the cutoff), the later part only validates. Only
the articles the rules purge are screened; the ones the rules keep stay kept whatever the
screen says, so they give examples and never scores.

Screens compared, each with its cutoff chosen on the calibration part alone:

- `legal_words`: keep every purged article with a legal word — the crude baseline;
- `fragment_rule`: the proposed patch — the title with each paragraph, embedded; keep
  when a fragment is at least `cutoff` alike to a relevant example fragment and more
  alike to it than to any irrelevant one;
- `fragment_margin`: the same fragments, scored by the best (relevant − irrelevant)
  likeness;
- `article_knn`: the article's start embedded whole, scored by its mean likeness to the
  five nearest relevant articles minus the five nearest irrelevant ones;
- `article_logistic`: a logistic regression on the article embeddings.

On calibration an article is never compared with its own fragments or itself.

    python -m evaluation.junk_screen.measure [MODEL_ID ...]
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from evaluation.junk_screen.corpus import LEGAL_WORDS
from semantic_retrieval.embeddings import EmbeddingSpend, create_text_embedder

ROOT = Path(__file__).resolve().parents[3]
SAMPLE = ROOT / "var" / "junk_screen" / "sample.jsonl"
LABELS = ROOT / "var" / "junk_screen" / "labels.jsonl"
OUT = ROOT / "var" / "junk_screen" / "measure.json"
# Calibration: published before this; validation: from it on.
SPLIT_AT = "2026-08-29"
# The patch's fragments: a paragraph, at most this long, with the title; at most this many.
FRAGMENT_CHARS = 1200
FRAGMENTS = 64
ARTICLE_CHARS = 1500
NEIGHBOURS = 5
# In the purged sample, one article stands for this many of its stratum in the corpus.
POPULATION = {"purged_legal": 4472, "purged_plain": 10387}
SAMPLED = {"purged_legal": 1400, "purged_plain": 250}
# The operating point: the lowest cutoff whose kept set on calibration is at least this
# often a real case — a review queue at least this useful.
MIN_PRECISION = 0.5


@dataclass(frozen=True)
class Article:
    article_id: int
    stratum: str
    published_at: str
    title: str
    text: str
    positive: bool
    kind: str
    political: bool
    legal_words: bool

    @property
    def purged(self) -> bool:
        return self.stratum != "kept"


def load() -> list[Article]:
    labels = {
        row["id"]: row for row in map(json.loads, LABELS.read_text(encoding="utf-8").splitlines())
    }
    articles = []
    for row in map(json.loads, SAMPLE.read_text(encoding="utf-8").splitlines()):
        label = labels.get(row["article_id"])
        if label is None:
            continue
        articles.append(
            Article(
                article_id=row["article_id"],
                stratum=row["stratum"],
                published_at=row["published_at"] or "",
                title=row["title"],
                text=row["text"],
                positive=label["kind"] == "case",
                kind=label["kind"],
                political=bool(label["political"]) and label["kind"] == "case",
                legal_words=row["legal_words"],
            )
        )
    return articles


def fragments(article: Article) -> list[str]:
    """The patch's fragments: the title with each paragraph."""
    parts = [
        f"{article.title}. {part[:FRAGMENT_CHARS]}"
        for part in article.text.split("\n")
        if part.strip()
    ]
    return (parts or [article.title])[:FRAGMENTS]


@dataclass
class Scored:
    """A purged article of one part and its score (higher: keep sooner)."""

    article: Article
    score: float


@dataclass(frozen=True)
class Outcome:
    screen: str
    model: str
    cutoff: float | None
    # Of the purged articles of the part.
    positives: int
    rescued: int
    kept: int
    kept_junk: int
    precision: float
    recall: float
    # The same, weighted up to the corpus's purged articles.
    rescued_corpus: float
    kept_junk_corpus: float
    political_positives: int
    political_rescued: int


def _weight(article: Article) -> float:
    return POPULATION[article.stratum] / SAMPLED[article.stratum]


def outcome(
    screen: str, model: str, cutoff: float | None, kept: Sequence[bool], articles: Sequence[Article]
) -> Outcome:
    positives = [a for a in articles if a.positive]
    rescued = [a for a, k in zip(articles, kept, strict=True) if k and a.positive]
    junk = [a for a, k in zip(articles, kept, strict=True) if k and not a.positive]
    held = len(rescued) + len(junk)
    return Outcome(
        screen=screen,
        model=model,
        cutoff=cutoff,
        positives=len(positives),
        rescued=len(rescued),
        kept=held,
        kept_junk=len(junk),
        precision=round(len(rescued) / held, 3) if held else 0.0,
        recall=round(len(rescued) / len(positives), 3) if positives else 0.0,
        rescued_corpus=round(sum(map(_weight, rescued)), 1),
        kept_junk_corpus=round(sum(map(_weight, junk)), 1),
        political_positives=sum(a.political for a in positives),
        political_rescued=sum(a.political for a in rescued),
    )


def choose_cutoff(scored: Sequence[Scored]) -> float:
    """The lowest score whose kept set (score ≥ it) is at least MIN_PRECISION real cases;
    the highest score when none is."""
    ranked = sorted(scored, key=lambda item: -item.score)
    best = ranked[0].score if ranked else 0.0
    hits = 0
    for position, item in enumerate(ranked, start=1):
        hits += item.article.positive
        if hits / position >= MIN_PRECISION:
            best = item.score
    return best


def _normalized(matrix: Any) -> Any:
    matrix = np.asarray(matrix, dtype=np.float32)
    return matrix / np.linalg.norm(matrix, axis=1, keepdims=True)


class Embedded:
    """Fragments and article starts of every article, embedded once per model."""

    def __init__(self, embed: Callable[[Sequence[str]], Any], articles: Sequence[Article]) -> None:
        self.owner: list[int] = []
        texts: list[str] = []
        self.legal: list[bool] = []
        for index, article in enumerate(articles):
            for fragment in fragments(article):
                self.owner.append(index)
                texts.append(fragment)
                self.legal.append(bool(LEGAL_WORDS.search(fragment)))
        self.fragments = _normalized(embed(texts))
        self.owner_array = np.asarray(self.owner)
        self.legal_array = np.asarray(self.legal)
        self.articles = _normalized(
            embed([f"{a.title}. {a.text[:ARTICLE_CHARS]}" for a in articles])
        )


def _fragment_scores(
    embedded: Embedded,
    articles: Sequence[Article],
    examples: Sequence[int],
    screened: Sequence[int],
) -> tuple[list[float], list[float]]:
    """Per screened article: the patch's best relevant likeness among the fragments that
    beat every irrelevant one (-1 when none does), and the best margin. Example fragments
    are the legal-word fragments of the example articles, never the article's own."""
    example_set = set(examples)
    is_example = np.array([owner in example_set for owner in embedded.owner]) & embedded.legal_array
    positive = np.array([articles[owner].positive for owner in embedded.owner])
    rule_scores, margin_scores = [], []
    for index in screened:
        own = embedded.owner_array == index
        mine = embedded.fragments[own]
        usable = is_example & ~own
        rel = embedded.fragments[usable & positive]
        irr = embedded.fragments[usable & ~positive]
        best_rel = (mine @ rel.T).max(axis=1)
        best_irr = (mine @ irr.T).max(axis=1)
        winning = best_rel > best_irr
        rule_scores.append(float(best_rel[winning].max()) if winning.any() else -1.0)
        margin_scores.append(float((best_rel - best_irr).max()))
    return rule_scores, margin_scores


def _knn_scores(
    embedded: Embedded,
    articles: Sequence[Article],
    examples: Sequence[int],
    screened: Sequence[int],
) -> list[float]:
    scores = []
    for index in screened:
        others = [e for e in examples if e != index]
        rel = [e for e in others if articles[e].positive]
        irr = [e for e in others if not articles[e].positive]
        me = embedded.articles[index]
        top_rel = np.sort(embedded.articles[rel] @ me)[-NEIGHBOURS:].mean()
        top_irr = np.sort(embedded.articles[irr] @ me)[-NEIGHBOURS:].mean()
        scores.append(float(top_rel - top_irr))
    return scores


def _logistic_scores(
    embedded: Embedded,
    articles: Sequence[Article],
    examples: Sequence[int],
    screened: Sequence[int],
    *,
    cross: bool,
) -> list[float]:
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import cross_val_predict

    x = embedded.articles[list(examples)]
    y = np.array([articles[e].positive for e in examples])
    model = LogisticRegression(max_iter=2000, class_weight="balanced", C=1.0)
    if cross:
        # Calibration scores its own examples: out of fold only.
        position = {e: i for i, e in enumerate(examples)}
        probs = cross_val_predict(model, x, y, cv=5, method="predict_proba")[:, 1]
        return [float(probs[position[index]]) for index in screened]
    model.fit(x, y)
    return [float(p) for p in model.predict_proba(embedded.articles[list(screened)])[:, 1]]


def run(
    models: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    spend: EmbeddingSpend | None = None,
) -> dict[str, Any]:
    env = os.environ if env is None else env
    spend = spend or EmbeddingSpend(budget_usd=1.0)
    articles = load()
    calibration = [i for i, a in enumerate(articles) if a.published_at < SPLIT_AT]
    validation = [i for i, a in enumerate(articles) if a.published_at >= SPLIT_AT]
    cal_purged = [i for i in calibration if articles[i].purged]
    val_purged = [i for i in validation if articles[i].purged]
    results: list[Outcome] = []
    summary: dict[str, Any] = {
        "articles": len(articles),
        "calibration": len(calibration),
        "validation": len(validation),
        "split_at": SPLIT_AT,
        "min_precision": MIN_PRECISION,
    }
    part = [articles[i] for i in val_purged]
    results.append(outcome("rules_only", "-", None, [False] * len(part), part))
    results.append(outcome("legal_words", "-", None, [a.legal_words for a in part], part))
    errors: dict[str, Any] = {}
    for model_id in models:
        embedder = create_text_embedder(env, model_id=model_id, spend=spend)
        embedded = Embedded(embedder.embed_documents, articles)
        name = model_id.split("/")[-1]
        rule_cal, margin_cal = _fragment_scores(embedded, articles, calibration, cal_purged)
        rule_val, margin_val = _fragment_scores(embedded, articles, calibration, val_purged)
        screens = {
            "fragment_rule": (rule_cal, rule_val),
            "fragment_margin": (margin_cal, margin_val),
            "article_knn": (
                _knn_scores(embedded, articles, calibration, cal_purged),
                _knn_scores(embedded, articles, calibration, val_purged),
            ),
            "article_logistic": (
                _logistic_scores(embedded, articles, calibration, cal_purged, cross=True),
                _logistic_scores(embedded, articles, calibration, val_purged, cross=False),
            ),
        }
        for screen, (cal_scores, val_scores) in screens.items():
            cal = [Scored(articles[i], s) for i, s in zip(cal_purged, cal_scores, strict=True)]
            cutoff = choose_cutoff(cal)
            kept = [s >= cutoff for s in val_scores]
            results.append(outcome(screen, name, round(cutoff, 4), kept, part))
            errors[f"{screen}/{name}"] = {
                "missed": [
                    {"id": a.article_id, "title": a.title[:120], "score": round(s, 4)}
                    for a, s, k in zip(part, val_scores, kept, strict=True)
                    if a.positive and not k
                ][:15],
                "kept_junk": [
                    {
                        "id": a.article_id,
                        "kind": a.kind,
                        "title": a.title[:120],
                        "score": round(s, 4),
                    }
                    for a, s, k in zip(part, val_scores, kept, strict=True)
                    if k and not a.positive
                ][:15],
                "curve": _curve(val_scores, part),
            }
    summary["results"] = [asdict(result) for result in results]
    summary["errors"] = errors
    summary["embedding_usage"] = {
        "calls": spend.calls,
        "prompt_tokens": spend.prompt_tokens,
        "cost_usd": round(spend.cost_usd, 6),
    }
    return summary


def _curve(scores: Sequence[float], part: Sequence[Article]) -> list[dict[str, float]]:
    """Recall and precision on validation at a few cutoffs, for the report."""
    points = []
    for quantile in (0.5, 0.7, 0.8, 0.9, 0.95):
        cutoff = float(np.quantile(scores, quantile))
        kept = [s >= cutoff for s in scores]
        hit = sum(k and a.positive for k, a in zip(kept, part, strict=True))
        held = sum(kept)
        positives = sum(a.positive for a in part)
        points.append(
            {
                "cutoff": round(cutoff, 4),
                "kept": held,
                "recall": round(hit / positives, 3) if positives else 0.0,
                "precision": round(hit / held, 3) if held else 0.0,
            }
        )
    return points


SCREEN_MODEL = ROOT / "src" / "monitoring" / "junk_screen_model.json"
LABELS_OUT = ROOT / "evaluation" / "junk_screen" / "labels.json"


def export(
    model_id: str,
    *,
    env: Mapping[str, str] | None = None,
    spend: EmbeddingSpend | None = None,
) -> dict[str, Any]:
    """The screen `purge-junk` uses: the logistic regression fitted on the calibration
    part, with the cutoff chosen there out of fold — the one the validation measured."""
    from sklearn.linear_model import LogisticRegression

    env = os.environ if env is None else env
    spend = spend or EmbeddingSpend(budget_usd=1.0)
    articles = load()
    calibration = [i for i, a in enumerate(articles) if a.published_at < SPLIT_AT]
    cal_purged = [i for i in calibration if articles[i].purged]
    embedder = create_text_embedder(env, model_id=model_id, spend=spend)
    embedded = Embedded(embedder.embed_documents, articles)
    cal_scores = _logistic_scores(embedded, articles, calibration, cal_purged, cross=True)
    cutoff = choose_cutoff(
        [Scored(articles[i], s) for i, s in zip(cal_purged, cal_scores, strict=True)]
    )
    model = LogisticRegression(max_iter=2000, class_weight="balanced", C=1.0)
    model.fit(embedded.articles[calibration], np.array([articles[i].positive for i in calibration]))
    screen = {
        "version": "junk-screen-v1",
        "model_id": model_id,
        "embedding_provider": (env.get("EMBEDDING_PROVIDER") or "local").strip().lower(),
        "article_chars": ARTICLE_CHARS,
        "coefficients": [round(float(c), 6) for c in model.coef_[0]],
        "intercept": round(float(model.intercept_[0]), 6),
        "cutoff": round(float(cutoff), 4),
        "trained_on": f"{len(calibration)} labelled articles published before {SPLIT_AT}",
    }
    SCREEN_MODEL.parent.mkdir(parents=True, exist_ok=True)
    SCREEN_MODEL.write_text(json.dumps(screen, ensure_ascii=False) + "\n", encoding="utf-8")
    labels = [
        {
            "article_id": a.article_id,
            "stratum": a.stratum,
            "published_at": a.published_at,
            "kind": a.kind,
            "political": a.political,
            "part": "calibration" if a.published_at < SPLIT_AT else "validation",
        }
        for a in articles
    ]
    LABELS_OUT.write_text(json.dumps(labels, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
    return {
        "cutoff": screen["cutoff"],
        "calibration": len(calibration),
        "embedding_usage": {
            "calls": spend.calls,
            "prompt_tokens": spend.prompt_tokens,
            "cost_usd": round(spend.cost_usd, 6),
        },
    }


if __name__ == "__main__" and sys.argv[1:2] == ["export"]:
    print(json.dumps(export(sys.argv[2] if len(sys.argv) > 2 else "intfloat/multilingual-e5-base")))
elif __name__ == "__main__":
    chosen = sys.argv[1:] or ["intfloat/multilingual-e5-base"]
    report = run(chosen)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    for row in report["results"]:
        print(
            f"{row['screen']:<17} {row['model']:<22} cutoff={row['cutoff']!s:<8} "
            f"rescued {row['rescued']}/{row['positives']} (recall {row['recall']}) "
            f"kept {row['kept']} junk {row['kept_junk']} precision {row['precision']} "
            f"| corpus: rescued≈{row['rescued_corpus']} junk≈{row['kept_junk_corpus']} "
            f"| political {row['political_rescued']}/{row['political_positives']}"
        )
