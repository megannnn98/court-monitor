"""The Jev strong pre-filter on the development OOF set of the Jev relevance experiments.

    python -m evaluation.jev_prefilter.experiment diagnostic
    python -m evaluation.jev_prefilter.experiment run
    python -m evaluation.jev_prefilter.experiment export
    python -m evaluation.jev_prefilter.experiment report

The set is frozen in `dataset.json`: the 1192 articles (81 relevant) Jev v1 and the atomic v2
were evaluated on — folds 1–3 of the v2 temporal CV over `court_monitor_v2/train.jsonl`,
without the diagnostic-30 and the few-shot examples. Their errors were read in those
experiments, so this is development data, not an independent evaluation.

`diagnostic` asks the diagnostic-30 (a technical check: the request, the parsing, the cost);
`run` asks the 1192 once. `export` and `report` read only the cache: `export` writes the
false negatives of the chosen policy and a fixed random sample of its drops for manual
reading; `report` adds the manual categories (`manual_categories.json`) when they exist.

Never read: the holdout of v1 or v2, the outer validation-401.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from evaluation.jev_prefilter.client import (
    MODEL,
    AnswerCache,
    Jev,
    JevAnswerError,
    cache_key,
    classify,
)
from evaluation.jev_prefilter.policy import POLICIES, decide
from evaluation.jev_prefilter.prompt import (
    DECISION,
    DIAGNOSTIC_OPTIONS,
    DROP,
    KEEP,
    PROMPT_VERSION,
    RUBRIC_VERSION,
    UNCERTAIN,
    article_text,
)

ROOT = Path(__file__).resolve().parents[3]
TRAIN = ROOT / "data/ml/relevance/court_monitor_v2/train.jsonl"
OUT_DIR = ROOT / "data/ml/relevance/jev_strong_prefilter"
DATASET = OUT_DIR / "dataset.json"
CACHE = OUT_DIR / "cache.jsonl"
RESULTS_DIR = OUT_DIR / "results"
MANUAL = RESULTS_DIR / "manual_categories.json"
FORBIDDEN = (
    ROOT / "data/ml/relevance/holdout",
    ROOT / "data/ml/relevance/holdout_v2",
    ROOT / "data/ml/relevance/court_monitor_v2/validation.jsonl",
)
# The v2 target the 1192 were labelled with.
LABEL = "relevant_to_court_monitor"
CONCURRENCY = 8
DROP_SAMPLE = 120
DROP_SAMPLE_SEED = 20261015
SNIPPET_CHARS = 600

# Fixed before the run.
MIN_RECALL = 0.97
CRITERIA = (
    ("EXCELLENT", 0.99, 0.50),
    ("GOOD", 0.98, 0.50),
    ("SUCCESS", 0.97, 0.40),
)
FN_CATEGORIES = (
    "speech",
    "antiwar",
    "political/civic",
    "journalism",
    "religion",
    "donation/support",
    "org/person confusion",
    "criminal/admin confusion",
    "no-new-event confusion",
    "treason",
    "infrastructure sabotage",
    "Furgal",
    "insufficient text",
    "other",
)
DROP_CATEGORIES = (
    "ordinary crime",
    "org-only",
    "administrative",
    "civil",
    "election",
    "digest",
    "no new event",
    "treason/espionage",
    "violence against people",
    "unrelated/no person",
    "other",
)


class ForbiddenDataError(RuntimeError):
    """The experiment tried to read the holdout or the outer validation."""


@dataclass(frozen=True)
class Article:
    article_id: int
    title: str
    text: str
    label: int


def allowed(path: Path) -> Path:
    resolved = path.resolve()
    for forbidden in FORBIDDEN:
        stop = forbidden.resolve()
        if resolved == stop or stop in resolved.parents:
            raise ForbiddenDataError(f"{path} is not development data")
    return path


def load_articles(train: Path, ids: Iterable[int]) -> dict[int, Article]:
    """The train articles of `ids`, by id; every id must be there."""
    wanted = set(ids)
    found: dict[int, Article] = {}
    for line in allowed(train).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row["article_id"] in wanted:
            if not isinstance(row[LABEL], bool):
                raise TypeError(f"{train}: {row['article_id']} has no true/false {LABEL}")
            found[row["article_id"]] = Article(
                row["article_id"], row["title"], row["text"], int(row[LABEL])
            )
    if missing := wanted - set(found):
        raise KeyError(f"articles missing from {train}: {sorted(missing)[:10]}")
    return found


def dataset() -> dict[str, Any]:
    manifest: dict[str, Any] = json.loads(allowed(DATASET).read_text(encoding="utf-8"))
    return manifest


def evaluated() -> list[Article]:
    items = dataset()["items"]
    articles = load_articles(TRAIN, (i["article_id"] for i in items))
    for item in items:
        if articles[item["article_id"]].label != item["label"]:
            raise ValueError(f"label of {item['article_id']} differs from the frozen manifest")
    return [articles[i["article_id"]] for i in items]


def _jev() -> Jev:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise SystemExit("OPENROUTER_API_KEY is not set")
    return Jev(lambda: httpx.Client(), key)


def ask_all(
    rows: Sequence[Article], jev: Jev, cache: AnswerCache
) -> tuple[dict[int, dict[str, Any]], int, dict[int, str]]:
    """Records by id, cache hits, and the articles whose answer was an error (they pass)."""

    def one(row: Article) -> tuple[int, dict[str, Any] | None, bool, str | None]:
        try:
            record, hit = classify(row.article_id, row.title, row.text, jev, cache)
        except JevAnswerError as exc:
            return row.article_id, None, False, str(exc)
        return row.article_id, record, hit, None

    records: dict[int, dict[str, Any]] = {}
    errors: dict[int, str] = {}
    hits = 0
    with ThreadPoolExecutor(CONCURRENCY) as pool:
        for article_id, record, hit, error in pool.map(one, rows):
            if record is None:
                errors[article_id] = error or "unknown"
            else:
                records[article_id] = record
                hits += hit
    return records, hits, errors


def cached_records(rows: Sequence[Article]) -> dict[int, dict[str, Any]]:
    """The cached answers of `rows` — no API call; a missing answer is simply absent."""
    cache = AnswerCache(CACHE)
    found: dict[int, dict[str, Any]] = {}
    for row in rows:
        record = cache.get(cache_key(MODEL, row.title, article_text(row.title, row.text)))
        if record is not None:
            found[row.article_id] = record
    return found


def _readings(records: Mapping[int, Mapping[str, Any]], article_id: int) -> Any:
    record = records.get(article_id)
    return None if record is None else record["readings"]


def policy_metrics(
    rows: Sequence[Article], records: Mapping[int, Mapping[str, Any]], policy: str
) -> dict[str, Any]:
    """Recall of the relevant articles that pass, and how much is dropped."""
    fn = fp = tp = tn = 0
    for row in rows:
        dropped = decide(_readings(records, row.article_id), policy) == "DROP"
        if row.label:
            fn += dropped
            tp += not dropped
        else:
            tn += dropped
            fp += not dropped
    total = len(rows)
    relevant = tp + fn
    drop_count = fn + tn
    return {
        "policy": policy,
        "recall": round(tp / relevant, 4) if relevant else 0.0,
        "FN": fn,
        "FP": fp,
        "drop_count": drop_count,
        "pass_count": total - drop_count,
        "drop_pct": round(100 * drop_count / total, 2) if total else 0.0,
        "pass_pct": round(100 * (total - drop_count) / total, 2) if total else 0.0,
    }


def verdict(metrics: Sequence[Mapping[str, Any]]) -> tuple[str, Mapping[str, Any] | None]:
    """The acceptance verdict and the chosen policy: the most aggressive with recall ≥ .97."""
    safe = [m for m in metrics if m["recall"] >= MIN_RECALL]
    if not safe:
        return "FAIL-RECALL", None
    chosen = max(safe, key=lambda m: (m["drop_count"], m["recall"]))
    for name, recall, drop in CRITERIA:
        if chosen["recall"] >= recall and chosen["drop_pct"] >= 100 * drop:
            return name, chosen
    return "FAIL-REDUCTION", chosen


def _probabilities(readings: Mapping[str, Any]) -> dict[str, float]:
    p = readings[DECISION]["probabilities"]
    return {KEEP: p[KEEP], DROP: p[DROP], UNCERTAIN: p[UNCERTAIN]}


def _diagnostics(readings: Mapping[str, Any]) -> dict[str, str]:
    return {name: readings[name]["choice"] for name in DIAGNOSTIC_OPTIONS}


def detail(row: Article, readings: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "article_id": row.article_id,
        "title": row.title,
        "gold": bool(row.label),
        "choice": readings[DECISION]["choice"],
        "probabilities": _probabilities(readings),
        **_diagnostics(readings),
        "snippet": row.text[:SNIPPET_CHARS],
    }


def distributions(readings: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    counters: dict[str, Counter[str]] = {name: Counter() for name in DIAGNOSTIC_OPTIONS}
    counters[DECISION] = Counter()
    for r in readings:
        for name, counter in counters.items():
            counter[r[name]["choice"]] += 1
    return {name: dict(counter.most_common()) for name, counter in counters.items()}


def drop_sample(
    rows: Sequence[Article], records: Mapping[int, Mapping[str, Any]], policy: str
) -> list[Article]:
    dropped = [r for r in rows if decide(_readings(records, r.article_id), policy) == "DROP"]
    rng = random.Random(DROP_SAMPLE_SEED)
    return sorted(rng.sample(dropped, min(DROP_SAMPLE, len(dropped))), key=lambda r: r.article_id)


def _write(name: str, payload: Any) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / name
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _spend(
    jev: Jev, records: Mapping[int, Any], hits: int, errors: Mapping[int, str]
) -> dict[str, Any]:
    return {
        "requested_model": jev.model,
        "answered_by": sorted({str(r["model_id"]) for r in records.values()}),
        "prompt_version": PROMPT_VERSION,
        "rubric_version": RUBRIC_VERSION,
        "articles": len(records) + len(errors),
        "answered": len(records),
        "errors": {str(k): v for k, v in sorted(errors.items())},
        "api_calls": jev.calls,
        "cache_hits": hits,
        "cost_usd_this_session": round(jev.cost_usd, 6),
    }


def diagnostic() -> dict[str, Any]:
    ids = dataset()["diagnostic"]
    articles = load_articles(TRAIN, ids)
    rows = [articles[i] for i in ids]
    jev = _jev()
    records, hits, errors = ask_all(rows, jev, AnswerCache(CACHE))
    report = {
        **_spend(jev, records, hits, errors),
        "note": "technical check only, not an evaluation",
        "distributions": distributions(r["readings"] for r in records.values()),
        "articles_detail": [
            {
                k: v
                for k, v in detail(r, records[r.article_id]["readings"]).items()
                if k != "snippet"
            }
            for r in rows
            if r.article_id in records
        ],
    }
    _write("diagnostic.json", report)
    return report


def run() -> dict[str, Any]:
    rows = evaluated()
    jev = _jev()
    records, hits, errors = ask_all(rows, jev, AnswerCache(CACHE))
    summary = _spend(jev, records, hits, errors)
    _write("run.json", summary)
    return summary


def _analysis(rows: Sequence[Article]) -> dict[str, Any]:
    records = cached_records(rows)
    metrics = [policy_metrics(rows, records, p) for p in POLICIES]
    outcome, chosen = verdict(metrics)
    return {"records": records, "metrics": metrics, "verdict": outcome, "chosen": chosen}


def export() -> dict[str, Any]:
    rows = evaluated()
    analysis = _analysis(rows)
    records, chosen = analysis["records"], analysis["chosen"]
    policy = chosen["policy"] if chosen else "argmax"
    fns = [
        detail(r, records[r.article_id]["readings"])
        for r in rows
        if r.label and decide(_readings(records, r.article_id), policy) == "DROP"
    ]
    sample = [
        detail(r, records[r.article_id]["readings"]) for r in drop_sample(rows, records, policy)
    ]
    payload = {"policy": policy, "false_negatives": fns, "drop_sample": sample}
    _write("for_manual_reading.json", payload)
    return {"policy": policy, "false_negatives": len(fns), "drop_sample": len(sample)}


def report() -> dict[str, Any]:
    manifest = dataset()
    rows = evaluated()
    analysis = _analysis(rows)
    records: Mapping[int, Mapping[str, Any]] = analysis["records"]
    chosen = analysis["chosen"]
    policy = chosen["policy"] if chosen else "argmax"
    manual: dict[str, Any] = (
        json.loads(MANUAL.read_text(encoding="utf-8")) if MANUAL.exists() else {}
    )
    fn_manual = manual.get("false_negatives", {})
    drop_manual = manual.get("drop_sample", {})
    fns = []
    for r in rows:
        if r.label and decide(_readings(records, r.article_id), policy) == "DROP":
            item = detail(r, records[r.article_id]["readings"])
            item.update(fn_manual.get(str(r.article_id), {}))
            fns.append(item)
    sample = drop_sample(rows, records, policy)
    sample_categories = Counter(
        drop_manual.get(str(r.article_id), {}).get("category", "uncategorised") for r in sample
    )
    dropped_readings = [
        records[r.article_id]["readings"]
        for r in rows
        if decide(_readings(records, r.article_id), policy) == "DROP"
    ]
    costs = [
        float(records[r.article_id].get("cost") or 0.0) for r in rows if r.article_id in records
    ]
    total_cost = sum(costs)
    inconsistent = [
        r.article_id
        for r in rows
        if r.article_id in records
        and max((p := _probabilities(records[r.article_id]["readings"])), key=lambda k: p[k])
        != records[r.article_id]["readings"][DECISION]["choice"]
    ]
    result = {
        "dataset": {
            "articles": manifest["articles"],
            "relevant": manifest["relevant"],
            "note": "development data (Jev OOF subset), NOT an independent evaluation",
        },
        "requested_model": MODEL,
        "answered_by": sorted({str(r["model_id"]) for r in records.values()}),
        "prompt_version": PROMPT_VERSION,
        "rubric_version": RUBRIC_VERSION,
        "answered": len(records),
        "unanswered_pass": len(rows) - len(records),
        "policies": analysis["metrics"],
        "verdict": analysis["verdict"],
        "chosen_policy": chosen,
        "false_negatives": fns,
        "drop_sample": {
            "policy": policy,
            "seed": DROP_SAMPLE_SEED,
            "size": len(sample),
            "categories": dict(sample_categories.most_common()),
        },
        "distributions": {
            "all": distributions(r["readings"] for r in records.values()),
            "dropped": distributions(dropped_readings),
            "false_negatives": distributions(records[f["article_id"]]["readings"] for f in fns),
        },
        "choice_not_argmax_of_probabilities": inconsistent,
        "cost": {
            # Each article priced by its cached answer: duplicate texts count once per article.
            # What was actually paid is in `run.json` and `diagnostic.json`.
            "cost_usd_per_article_sum": round(total_cost, 6),
            "cost_per_article": round(total_cost / len(costs), 7) if costs else 0.0,
            "cost_per_1000": round(1000 * total_cost / len(costs), 4) if costs else 0.0,
            "deepseek_call_reduction_pct": chosen["drop_pct"] if chosen else 0.0,
        },
    }
    _write("report.json", result)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("diagnostic", "run", "export", "report"))
    args = parser.parse_args(argv)
    command = {"diagnostic": diagnostic, "run": run, "export": export, "report": report}
    result = command[args.command]()
    print(json.dumps(result, ensure_ascii=False, indent=2)[:6000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
