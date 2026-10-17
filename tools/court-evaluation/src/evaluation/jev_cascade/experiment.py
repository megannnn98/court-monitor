"""The junk screen with a second Jev behind it: chosen on dev, judged once on a fresh set.

    python -m evaluation.jev_cascade.experiment dev      # no API call: the caches of 2026-10-15
    python -m evaluation.jev_cascade.experiment freeze   # the chosen policies, before fresh
    python -m evaluation.jev_cascade.experiment fresh    # asks JEV-2 about the fresh 400 once

JEV-1 is the production decision screen (`jev-screen-v1`, a probability of «case», kept at or
above a cutoff). JEV-2 is the production drop verifier (`jev-strong-prefilter-v1`): asked only
about what JEV-1 drops, it lets an article go on DROP with P(DROP) >= a threshold. An article
is dropped by the cascade only when both drop it; a failure of JEV-2 keeps it.

What is kept is the positive class of the gate: TP a relevant article passed on to the next
(DeepSeek) stage, FN a relevant article dropped for good.

Dev — the 1192 OOF articles (81 relevant) of the Jev experiments, both Jevs already asked on
2026-10-15: JEV-1's scores in `jev_strong_prefilter/results/prod_screen_scores.json`, JEV-2's
answers in `jev_strong_prefilter/cache.jsonl`. Development data: its errors were read.

Fresh — the 400 articles of 2026-09-20..24 (103 relevant, gold sha `bd3aff8f…`) of the DeepSeek
context validation. JEV-1 was asked about them on 2026-10-15 (`jev_scores.json`); JEV-2 never.
Nothing was tuned on them; `fresh` refuses to run before `freeze` and runs once.

The grid of thresholds is the one fixed before the runs: JEV-1 cutoffs `CUTOFFS` (the
production 0.5 and the lower ones looked at for the borderline band), JEV-2 `P_DROPS` (the
strong pre-filter's own). Selection on dev, before fresh:

- B, JEV-1 alone: the cutoff dropping the most with dev recall >= `MIN_RECALL`;
- C, the cascade: the highest dev recall, then the most dropped.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from collections.abc import Mapping, Sequence
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
from processing.junk.decision_screen import CUTOFF as PRODUCTION_CUTOFF
from processing.junk.drop_verifier import (
    ERROR,
    MIN_P_DROP,
    Verdict,
    VerifierAnswerError,
    verdict_of,
)
from processing.junk.drop_verifier_prompt import DROP, PROMPT_VERSION, article_text, request_for

ROOT = Path(__file__).resolve().parents[5]
RELEVANCE = ROOT / "data/ml/relevance"
DEV_DIR = RELEVANCE / "jev_strong_prefilter"
DEV_DATASET = DEV_DIR / "dataset.json"
DEV_TRAIN = RELEVANCE / "court_monitor_v2/train.jsonl"
DEV_JEV1 = DEV_DIR / "results/prod_screen_scores.json"
DEV_JEV2 = DEV_DIR / "cache.jsonl"
FRESH_DIR = RELEVANCE / "deepseek_context_validation/fresh"
FRESH_ARTICLES = FRESH_DIR / "articles.jsonl"
FRESH_GOLD = FRESH_DIR / "gold.jsonl"
FRESH_JEV1 = FRESH_DIR / "jev_scores.json"
FRESH_ARTICLES_SHA = "36c68eb17852736f31e2b96a52255bf3fe5f7c80e72d1dd17861bb2c95810436"
FRESH_GOLD_SHA = "bd3aff8f1be43fbd1b3e61b411dbea9187aeecdc104f0499673d85323d6553c7"
OUT_DIR = RELEVANCE / "jev_cascade"
FRESH_CACHE = OUT_DIR / "fresh_jev2_cache.jsonl"
FREEZE = OUT_DIR / "freeze.json"
RESULTS = OUT_DIR / "results"

CUTOFFS = (0.5, 0.3, 0.2, 0.1, 0.05, 0.03)
P_DROPS = (0.5, 0.6, 0.7, 0.8, 0.9)
MIN_RECALL = 0.97
CONCURRENCY = 8
# The strong pre-filter's own choice on dev (2026-10-15), for JEV-2 asked alone.
JEV2_ALONE_P_DROP = 0.8


@dataclass(frozen=True)
class Case:
    article_id: int
    title: str
    relevant: bool
    jev1: float
    # JEV-2's answer; None where it was never asked.
    jev2: Verdict | None


def keeps(case: Case, cutoff: float | None, p_drop: float | None) -> bool:
    """Whether the gate passes the article on. `cutoff` None: JEV-2 alone; `p_drop` None:
    JEV-1 alone. With both, the cascade: dropped only when both drop it."""
    if cutoff is not None and case.jev1 >= cutoff:
        return True
    if p_drop is None:
        return False
    verdict = case.jev2
    if verdict is None:
        raise ValueError(f"JEV-2 was not asked about {case.article_id}")
    return not (
        verdict.decision == DROP and verdict.p_drop is not None and verdict.p_drop >= p_drop
    )


def gate_metrics(
    cases: Sequence[Case], cutoff: float | None, p_drop: float | None
) -> dict[str, Any]:
    tp = fp = tn = fn = 0
    for case in cases:
        kept = keeps(case, cutoff, p_drop)
        tp += kept and case.relevant
        fp += kept and not case.relevant
        fn += not kept and case.relevant
        tn += not kept and not case.relevant
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    total = len(cases)
    return {
        "cutoff": cutoff,
        "p_drop": p_drop,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "passed": tp + fp,
        "dropped": tn + fn,
        "passed_share": round((tp + fp) / total, 4) if total else 0.0,
        "dropped_share": round((tn + fn) / total, 4) if total else 0.0,
        "lost_ids": sorted(
            c.article_id for c in cases if c.relevant and not keeps(c, cutoff, p_drop)
        ),
    }


def cascade_counts(cases: Sequence[Case], cutoff: float, p_drop: float) -> dict[str, Any]:
    """The counters the production cascade logs, and the rescue of relevant articles."""
    jev1_drop = [c for c in cases if c.jev1 < cutoff]
    confirmed = [c for c in jev1_drop if not keeps(c, cutoff, p_drop)]
    errors = [c for c in jev1_drop if c.jev2 is not None and c.jev2.decision == ERROR]
    false_drops = [c for c in jev1_drop if c.relevant]
    lost = [c for c in confirmed if c.relevant]
    return {
        "total": len(cases),
        "jev1_keep": len(cases) - len(jev1_drop),
        "jev1_drop": len(jev1_drop),
        "jev2_called": len(jev1_drop),
        "jev2_keep_rescue": len(jev1_drop) - len(confirmed),
        "jev2_drop_confirmed": len(confirmed),
        "jev2_error": len(errors),
        "final_keep": len(cases) - len(confirmed),
        "final_drop": len(confirmed),
        "jev1_false_drops": len(false_drops),
        "jev1_false_drop_ids": sorted(c.article_id for c in false_drops),
        "rescued_by_jev2": len(false_drops) - len(lost),
        "still_lost": len(lost),
        "still_lost_ids": sorted(c.article_id for c in lost),
        "jev2_rescued_junk": sum(
            1 for c in jev1_drop if not c.relevant and keeps(c, cutoff, p_drop)
        ),
    }


def choose(cases: Sequence[Case]) -> dict[str, Any]:
    """The pre-declared choice on dev (see the module's docstring)."""
    alone = [gate_metrics(cases, c, None) for c in CUTOFFS]
    eligible = [m for m in alone if m["recall"] >= MIN_RECALL]
    b = max(eligible, key=lambda m: (m["dropped"], m["recall"])) if eligible else None
    cascades = [gate_metrics(cases, c, p) for c in CUTOFFS for p in P_DROPS]
    best = max(cascades, key=lambda m: (m["recall"], m["dropped"]))
    return {
        "jev1_alone": {"cutoff": None if b is None else b["cutoff"]},
        "cascade": {"cutoff": best["cutoff"], "p_drop": best["p_drop"]},
    }


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _write(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _verdict(readings: Mapping[str, Any] | None, error: str | None = None) -> Verdict:
    """As the production cascade reads an answer; an unreadable one is `ERROR` (kept)."""
    if readings is None:
        return Verdict(decision=ERROR, error=error or "no answer")
    try:
        return verdict_of(readings)
    except VerifierAnswerError as exc:
        return Verdict(decision=ERROR, error=str(exc))


def dev_cases() -> list[Case]:
    manifest = json.loads(DEV_DATASET.read_text(encoding="utf-8"))
    labels = {int(i["article_id"]): bool(i["label"]) for i in manifest["items"]}
    texts = {int(r["article_id"]): r for r in _jsonl(DEV_TRAIN) if int(r["article_id"]) in labels}
    scores = json.loads(DEV_JEV1.read_text(encoding="utf-8"))
    if scores["cutoff"] != PRODUCTION_CUTOFF or scores["errors"]:
        raise ValueError(f"{DEV_JEV1} is not a complete production run")
    cache = AnswerCache(DEV_JEV2)
    cases = []
    for article_id, relevant in labels.items():
        row = texts[article_id]
        title, body = row["title"] or "", row["text"] or ""
        record = cache.get(cache_key(MODEL, title, article_text(title, body)))
        cases.append(
            Case(
                article_id,
                title,
                relevant,
                float(scores["scores"][str(article_id)]),
                _verdict(None if record is None else record["readings"]),
            )
        )
    return cases


def _report(cases: Sequence[Case]) -> dict[str, Any]:
    return {
        "articles": len(cases),
        "relevant": sum(c.relevant for c in cases),
        "jev1_alone": [gate_metrics(cases, c, None) for c in CUTOFFS],
        "jev2_alone": [gate_metrics(cases, None, p) for p in P_DROPS],
        "cascade": [
            gate_metrics(cases, c, p) | {"counts": cascade_counts(cases, c, p)}
            for c in CUTOFFS
            for p in P_DROPS
        ],
    }


def dev() -> dict[str, Any]:
    cases = dev_cases()
    report = _report(cases) | {"choice": choose(cases)}
    _write(RESULTS / "dev.json", report)
    return report


def _request_sha() -> str:
    request = json.dumps(request_for(""), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(request.encode("utf-8")).hexdigest()


def freeze() -> dict[str, Any]:
    if FREEZE.exists():
        raise SystemExit(f"{FREEZE} exists: the choice is frozen")
    choice = choose(dev_cases())
    if choice["cascade"] != {"cutoff": PRODUCTION_CUTOFF, "p_drop": MIN_P_DROP}:
        raise SystemExit(f"dev chose {choice['cascade']}, production has another: fix it first")
    frozen = {
        "frozen_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "choice": choice,
        "jev2_alone_p_drop": JEV2_ALONE_P_DROP,
        "production_cutoff": PRODUCTION_CUTOFF,
        "jev2_prompt_version": PROMPT_VERSION,
        "jev2_request_sha256": _request_sha(),
        "jev2_model": MODEL,
        "dev": {"dataset_sha256": _sha(DEV_DATASET), "jev1_sha256": _sha(DEV_JEV1)},
        "fresh": {"articles_sha256": FRESH_ARTICLES_SHA, "gold_sha256": FRESH_GOLD_SHA},
    }
    _write(FREEZE, frozen)
    return frozen


def _fresh_rows() -> tuple[list[dict[str, Any]], dict[int, bool], dict[str, float]]:
    if _sha(FRESH_ARTICLES) != FRESH_ARTICLES_SHA or _sha(FRESH_GOLD) != FRESH_GOLD_SHA:
        raise SystemExit("the fresh set or its gold changed")
    gold = {int(r["article_id"]): bool(r["relevant"]) for r in _jsonl(FRESH_GOLD)}
    scores = json.loads(FRESH_JEV1.read_text(encoding="utf-8"))
    if scores["cutoff"] != PRODUCTION_CUTOFF:
        raise SystemExit(f"{FRESH_JEV1} is not the production screen")
    return _jsonl(FRESH_ARTICLES), gold, scores["scores"]


def fresh() -> dict[str, Any]:
    if not FREEZE.exists():
        raise SystemExit("freeze first: the fresh set is judged only by a frozen choice")
    frozen = json.loads(FREEZE.read_text(encoding="utf-8"))
    if frozen["jev2_request_sha256"] != _request_sha():
        raise SystemExit("JEV-2's request changed since the freeze")
    if (RESULTS / "fresh.json").exists():
        raise SystemExit("the fresh set was judged already")
    rows, gold, jev1 = _fresh_rows()
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise SystemExit("OPENROUTER_API_KEY is not set")
    jev = Jev(lambda: httpx.Client(), key)
    cache = AnswerCache(FRESH_CACHE)

    def one(row: dict[str, Any]) -> tuple[int, Verdict, float, bool]:
        started = time.monotonic()
        try:
            record, hit = classify(row["id"], row["title"] or "", row["text"] or "", jev, cache)
        except JevAnswerError as exc:
            return row["id"], _verdict(None, str(exc)), time.monotonic() - started, False
        return row["id"], _verdict(record["readings"]), time.monotonic() - started, hit

    # JEV-2 is asked about all 400 so that it can be judged alone too; the cascade reads
    # only its answers about what JEV-1 drops.
    with ThreadPoolExecutor(CONCURRENCY) as pool:
        answered = {i: (v, s, h) for i, v, s, h in pool.map(one, rows)}
    cases = [
        Case(
            r["id"],
            r["title"] or "",
            gold[r["id"]],
            float(jev1[str(r["id"])]),
            answered[r["id"]][0],
        )
        for r in rows
    ]
    asked = [s for _, s, hit in answered.values() if not hit]
    choice = frozen["choice"]
    cut, p = choice["cascade"]["cutoff"], choice["cascade"]["p_drop"]
    jev1_drops = {c.article_id for c in cases if c.jev1 < cut}
    jev1_cost = json.loads(FRESH_JEV1.read_text(encoding="utf-8"))["cost_usd"]
    jev2_cost_all = sum(float(r.get("cost") or 0) for r in _jsonl(FRESH_CACHE))
    jev2_cost_cascade = sum(
        float(r.get("cost") or 0) for r in _jsonl(FRESH_CACHE) if int(r["article_id"]) in jev1_drops
    )
    report = _report(cases) | {
        "frozen": frozen,
        "variants": {
            "A_production_jev1_cutoff_0.5": gate_metrics(cases, PRODUCTION_CUTOFF, None),
            "B_jev1_alone_dev_cutoff": gate_metrics(cases, choice["jev1_alone"]["cutoff"], None),
            "C_cascade": gate_metrics(cases, cut, p) | {"counts": cascade_counts(cases, cut, p)},
            "D_jev2_alone": gate_metrics(cases, None, frozen["jev2_alone_p_drop"]),
        },
        "spend": {
            "jev1_calls": len(cases),
            "jev1_cost_usd": round(jev1_cost, 6),
            "jev2_api_calls": jev.calls,
            "jev2_asked_all": len(cases),
            "jev2_called_in_cascade": len(jev1_drops),
            "jev2_cost_usd_all_400": round(jev2_cost_all, 6),
            "jev2_cost_usd_cascade": round(jev2_cost_cascade, 6),
            "jev2_errors": sorted(
                c.article_id for c in cases if c.jev2 and c.jev2.decision == ERROR
            ),
            "jev2_latency_s": {
                "asked": len(asked),
                "mean": round(sum(asked) / len(asked), 3) if asked else None,
                "max": round(max(asked), 3) if asked else None,
            },
        },
        "per_article": [
            {
                "article_id": c.article_id,
                "relevant": c.relevant,
                "jev1_score": c.jev1,
                "jev1": "keep" if c.jev1 >= cut else "drop",
                "jev2_invoked": c.jev1 < cut,
                "jev2_decision": c.jev2.decision if c.jev2 else None,
                "jev2_p_drop": c.jev2.p_drop if c.jev2 else None,
                "jev2_confidence": c.jev2.confidence if c.jev2 else None,
                "final": "keep" if keeps(c, cut, p) else "drop",
                "title": c.title[:120],
            }
            for c in cases
        ],
    }
    _write(RESULTS / "fresh.json", report)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evaluation.jev_cascade.experiment")
    parser.add_argument("command", choices=("dev", "freeze", "fresh"))
    command = parser.parse_args(argv).command
    result = {"dev": dev, "freeze": freeze, "fresh": fresh}[command]()
    summary = {
        k: v
        for k, v in result.items()
        if k not in ("per_article", "cascade", "jev1_alone", "jev2_alone")
    }
    json.dump(summary, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
