"""Semantic Retrieval v2: derived reports (no database, no model).

Reads dev.json, validation.json, downstream.json, perf_*.json and the judgments,
writes the per-model views, sweeps, error lists and comparison.{json,md}.

    PYTHONPATH=src uv run python -m evaluation.semantic_v2.report
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from evaluation.real_world.retrieval_eval import RealRetrievalQuery, load_retrieval_queries
from evaluation.semantic_v2.retrieval import (
    REPORT_DIR,
    RUN_SPLITS,
    _write_json,
    all_judgments,
    load_pool_judgments,
    load_retired,
    load_runs,
    runnable,
)

MODELS = ("e5", "bge_m3")
TOP = 10


def _load(name: str) -> dict[str, Any]:
    return dict(json.loads((REPORT_DIR / name).read_text("utf-8")))


def model_view(split_report: Mapping[str, Any], model: str) -> dict[str, Any]:
    systems = split_report["ranking"]["systems"]
    return {
        "split": split_report["split"],
        "status": split_report["status"],
        "model": model,
        "ranking": {s: systems[s] for s in (f"{model}_dense", f"{model}_hybrid")},
        "similarity": split_report["similarity"][model],
        "no_reachable_relevant": split_report["ranking"]["no_reachable_relevant"],
    }


def retrieval_errors(
    queries: Sequence[RealRetrievalQuery],
    runs: Mapping[str, Mapping[str, Sequence[Any]]],
    judgments: Mapping[str, Mapping[str, int]],
    system: str,
    threshold: float,
) -> list[dict[str, Any]]:
    """Positive queries with a relevant key outside the top 10 or below the threshold;
    negative queries with an accepted entity."""
    errors: list[dict[str, Any]] = []
    for query in queries:
        ranking = runs[system][query.query_id]
        judged = judgments[query.query_id]
        accepted = [r for r in ranking if (r.dense or -1.0) >= threshold]
        if query.expected_no_match:
            if accepted:
                errors.append(
                    {
                        "query_id": query.query_id,
                        "text": query.text,
                        "kind": "negative_accepted",
                        "accepted": len(accepted),
                        "top_accepted": [[r.key, r.dense] for r in accepted[:3]],
                    }
                )
            continue
        position = {r.key: i for i, r in enumerate(ranking, 1)}
        similarity = {r.key: r.dense for r in ranking}
        missed = [
            {"key": k, "grade": g, "rank": position.get(k), "similarity": similarity.get(k)}
            for k, g in judged.items()
            if g > 0 and (position.get(k, 10**6) > TOP or (similarity.get(k) or -1.0) < threshold)
        ]
        wrong_top = [r.key for r in ranking[:3] if judged.get(r.key) == 0]
        if missed or wrong_top:
            errors.append(
                {
                    "query_id": query.query_id,
                    "text": query.text,
                    "tags": query.tags,
                    "kind": "positive",
                    "missed_or_rejected": missed,
                    "judged_not_relevant_in_top3": wrong_top,
                }
            )
    return errors


def downstream_cases(
    queries: Sequence[RealRetrievalQuery],
    judgments: Mapping[str, Mapping[str, int]],
    downstream: Mapping[str, Any],
    e5_name: str,
    bge_name: str,
) -> dict[str, Any]:
    """E5 correct / BGE wrong and the reverse, per query (correct: every relevant person
    returned and no judged non-relevant one; a negative query: nothing returned)."""
    buckets = ("e5_correct_bge_wrong", "bge_correct_e5_wrong", "both_wrong", "both_correct")
    cases: dict[str, list[dict[str, Any]]] = {b: [] for b in buckets}
    # The same split judged on recall alone: every relevant person reached the report.
    recall_cases: dict[str, list[str]] = {b: [] for b in buckets}
    configs = downstream["configurations"]
    for query in queries:
        split = query.split.value
        e5 = configs[e5_name][split]["returned"].get(query.query_id)
        bge = configs[bge_name][split]["returned"].get(query.query_id)
        if e5 is None or bge is None:
            continue
        judged = judgments[query.query_id]
        relevant = {k for k, g in judged.items() if g > 0}
        if not query.expected_no_match and not relevant:
            continue

        def verdict(
            returned: list[str],
            query: RealRetrievalQuery = query,
            judged: Mapping[str, int] = judged,
            relevant: set[str] = relevant,
        ) -> tuple[bool, dict[str, Any]]:
            if query.expected_no_match:
                return not returned, {"returned": len(returned), "top5": returned[:5]}
            missed = sorted(relevant - set(returned))
            false_positive = [k for k in returned if judged.get(k) == 0]
            # Sorted: the committed comparison must be byte-identical between runs.
            ranks = {k: returned.index(k) + 1 for k in sorted(relevant) if k in returned}
            return (not missed and not false_positive), {
                "returned": len(returned),
                "missed": missed,
                "judged_false_positive": false_positive[:10],
                "judged_false_positive_count": len(false_positive),
                "unjudged_returned": sum(1 for k in returned if k not in judged),
                "relevant_ranks": ranks,
                "relevant_in_top5": sum(1 for r in ranks.values() if r <= 5),
                "relevant_in_top10": sum(1 for r in ranks.values() if r <= 10),
            }

        def bucket_of(ok_e5: bool, ok_bge: bool) -> str:
            if ok_e5 and ok_bge:
                return "both_correct"
            if ok_e5:
                return "e5_correct_bge_wrong"
            return "bge_correct_e5_wrong" if ok_bge else "both_wrong"

        ok_e5, detail_e5 = verdict(e5)
        ok_bge, detail_bge = verdict(bge)
        bucket = bucket_of(ok_e5, ok_bge)
        if query.expected_no_match:
            recall_bucket = bucket
        else:
            recall_bucket = bucket_of(not detail_e5["missed"], not detail_bge["missed"])
        recall_cases[recall_bucket].append(query.query_id)
        cases[bucket].append(
            {
                "query_id": query.query_id,
                "split": split,
                "text": query.text,
                "negative": query.expected_no_match,
                "e5": detail_e5,
                "bge_m3": detail_bge,
            }
        )
    return {
        "rule": "correct = all relevant returned and no judged non-relevant (negative: nothing)",
        "counts": {k: len(v) for k, v in cases.items()},
        "cases": cases,
        "recall_only": {
            "rule": "correct = all relevant returned (negative: nothing returned)",
            "counts": {k: len(v) for k, v in recall_cases.items()},
            "queries": recall_cases,
        },
    }


def _row(metrics: Mapping[str, Any], names: Sequence[str]) -> str:
    return " | ".join(str(metrics.get(name, "—")) for name in names)


def comparison_markdown(comparison: Mapping[str, Any]) -> str:
    lines = [
        "# Semantic Retrieval v2 — E5 vs BGE-M3 (PRELIMINARY)",
        "",
        "Golden articles and relevance judgments are agent DRAFT; nothing here is human-verified.",
        "Unjudged entities are never counted as not relevant: recall@100 is a lower bound,",
        "coverage@k is the judged share of the top k. The test split was not run.",
        "",
    ]
    ranking_cols = [
        "queries",
        "mrr",
        "recall@5",
        "recall@10",
        "recall@20",
        "recall@100",
        "ndcg@5",
        "ndcg@10",
        "coverage@10",
        "coverage@100",
    ]
    for split in ("dev", "validation"):
        lines += [f"## Ranking — {split}", ""]
        for category, systems in comparison["ranking"][split].items():
            lines += [
                f"### {category}",
                "",
                "| system | " + " | ".join(ranking_cols) + " |",
                "|---" * (len(ranking_cols) + 1) + "|",
            ]
            for system, metrics in systems.items():
                lines.append(f"| {system} | {_row(metrics, ranking_cols)} |")
            lines.append("")
        lines += [
            f"### Paired bootstrap (95% CI of the mean per-query difference) — {split}",
            "",
            "| pair | metric | mean diff | CI low | CI high | queries |",
            "|---|---|---|---|---|---|",
        ]
        for pair, metrics in comparison["paired_bootstrap"][split].items():
            for name, m in metrics.items():
                lines.append(
                    f"| {pair} | {name} | {m['mean_diff']} | {m['ci95_low']} | "
                    f"{m['ci95_high']} | {m['queries']} |"
                )
        lines.append("")
    acc_cols = [
        "recall_micro",
        "recall_macro",
        "precision_judged",
        "accepted_unjudged",
        "judged_share_of_accepted",
        "rejection_rate",
        "hard_negative_fp",
        "offtopic_fp",
    ]
    lines += [
        "## Acceptance (dense similarity threshold)",
        "",
        f"Dev baseline E5@0.80 and the Pareto selection: {comparison['selected_thresholds']}.",
        "",
        "| split | configuration | " + " | ".join(acc_cols) + " |",
        "|---" * (len(acc_cols) + 2) + "|",
    ]
    for split, configs in comparison["acceptance"].items():
        # The dev-selected E5 threshold is the baseline itself, so several names describe
        # the same configuration: one row per distinct result, names joined.
        merged: dict[tuple[object, ...], list[str]] = {}
        metrics_of: dict[tuple[object, ...], Mapping[str, Any]] = {}
        for name, metrics in configs.items():
            signature = tuple(metrics[column] for column in acc_cols)
            merged.setdefault(signature, []).append(name)
            metrics_of[signature] = metrics
        for signature, names in merged.items():
            lines.append(
                f"| {split} | {' = '.join(names)} | {_row(metrics_of[signature], acc_cols)} |"
            )
    down_cols = [
        "recall_micro",
        "recall_macro",
        "precision_judged",
        "returned_unjudged",
        "returned_median",
        "empty_result_positive_queries",
        "negative_rejection_rate",
        "negative_persons_returned",
    ]
    lines += [
        "",
        "## Research Workflow (deterministic parser, hybrid + acceptance)",
        "",
        "| split | configuration | " + " | ".join(down_cols) + " | latency p50 ms |",
        "|---" * (len(down_cols) + 3) + "|",
    ]
    for name, entry in comparison["downstream"]["configurations"].items():
        for split in ("dev", "validation"):
            m = entry[split]
            lines.append(f"| {split} | {name} | {_row(m, down_cols)} | {m['latency_ms']['p50']} |")
    lines += ["", "Research benchmark semantic cases (claims judged against the golden set):", ""]
    for name, entry in comparison["downstream"]["configurations"].items():
        bench = entry["research_benchmark_semantic_cases"]
        lines.append(f"- {name}: persons {bench['persons']}, claims {bench['claims']}")
    cases = comparison["downstream_cases"]
    outcome_line = (
        f"Per-query outcome, E5@0.80 vs BGE-M3 calibrated ({cases['rule']}): {cases['counts']}"
    )
    recall_line = f"Recall only ({cases['recall_only']['rule']}): {cases['recall_only']['counts']}"
    lines += ["", outcome_line, "", recall_line, ""]
    perf_cols = [
        "dimension",
        "model_load_seconds",
        "full_corpus_embedding_seconds",
        "documents_per_second",
        "full_rebuild_seconds",
        "vram_peak_mib",
        "ram_peak_mib",
        "event_hnsw_index_bytes",
    ]
    lines += [
        "## Performance (RTX 3060, same 805 documents)",
        "",
        "| model | "
        + " | ".join(perf_cols)
        + " | query emb p50/p95 ms | dense PERSON p50/p95 ms |",
        "|---" * (len(perf_cols) + 3) + "|",
    ]
    for model, perf in comparison["performance"].items():
        q, d = perf["query_embedding_ms"], perf["dense_retrieval_ms"]["person"]
        lines.append(
            f"| {model} | {_row(perf, perf_cols)} | {q['p50']}/{q['p95']} | {d['p50']}/{d['p95']} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    retired = load_retired()
    queries = [q for q in runnable(load_retrieval_queries()) if q.query_id not in retired]
    judgments = all_judgments(queries, load_pool_judgments())
    runs = load_runs()
    splits = {split.value: _load(f"{split.value}.json") for split in RUN_SPLITS}
    selected = _load("selected_thresholds.json")
    downstream = _load("downstream.json")

    for model in MODELS:
        for split, report in splits.items():
            _write_json(REPORT_DIR / f"{model}_{split}.json", model_view(report, model))
        sweep = splits["dev"]["acceptance"]["sweeps"][model]
        _write_json(
            REPORT_DIR / f"threshold_sweep_{model}.json",
            {
                "split": "dev",
                "status": "PRELIMINARY (DRAFT judgments)",
                "rule": "max recall with precision_judged, rejection_rate and hard_negative_fp "
                "no worse than E5@0.80 on dev; ties -> higher threshold",
                "baseline": splits["dev"]["acceptance"]["baseline"],
                "selected": sweep["selected"],
                "rows": sweep["rows"],
            },
        )
        _write_json(
            REPORT_DIR / f"errors_{model}.json",
            {
                split: retrieval_errors(
                    [q for q in queries if q.split.value == split],
                    runs,
                    judgments,
                    f"{model}_dense",
                    selected[model],
                )
                for split in splits
            },
        )

    unreachable = {
        query_id for r in splits.values() for query_id in r["ranking"]["no_reachable_relevant"]
    }
    names = list(downstream["configurations"])
    e5_name = names[0]
    bge_name = next(n for n in names if n.startswith("bge_m3"))
    comparison: dict[str, Any] = {
        "status": "PRELIMINARY (DRAFT judgments)",
        "selected_thresholds": selected,
        "ranking": {
            split: {
                category: {
                    system: metrics[category]
                    for system, metrics in report["ranking"]["systems"].items()
                    if category in metrics
                }
                for category in ("all", "person", "event", "semantic_only", "group", "hard")
            }
            for split, report in splits.items()
        },
        "paired_bootstrap": {split: r["paired_bootstrap"] for split, r in splits.items()},
        "acceptance": {
            "dev": {
                "e5@0.8 (baseline)": splits["dev"]["acceptance"]["baseline"],
                **{
                    f"{m}@{selected[m]} (selected)": splits["dev"]["acceptance"]["sweeps"][m][
                        "selected"
                    ]
                    for m in MODELS
                },
            },
            "validation": {
                f"{name} {system}": metrics
                for name, per_system in splits["validation"]["acceptance"].items()
                for system, metrics in per_system.items()
            },
        },
        "downstream": {
            "configurations": {
                name: {
                    **{k: v for k, v in entry.items() if k not in ("dev", "validation")},
                    **{
                        split: {k: v for k, v in entry[split].items() if k != "returned"}
                        for split in ("dev", "validation")
                    },
                }
                for name, entry in dict(downstream["configurations"]).items()
            }
        },
        # Queries whose relevant entities were never extracted cannot be answered by either model.
        "downstream_cases": downstream_cases(
            [q for q in queries if q.query_id not in unreachable],
            judgments,
            downstream,
            e5_name,
            bge_name,
        ),
        "performance": {m: _load(f"perf_{m}.json") for m in MODELS},
    }
    _write_json(REPORT_DIR / "comparison.json", comparison)
    (REPORT_DIR / "comparison.md").write_text(comparison_markdown(comparison), "utf-8")
    print(comparison["downstream_cases"]["counts"])


if __name__ == "__main__":
    main()
