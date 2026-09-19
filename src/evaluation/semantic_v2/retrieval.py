"""Semantic Retrieval v2: rankings, candidate pool, metrics with unjudged entities.

Judgments are by key: a golden person id, a golden event "case_id/event_id", or an
`extra-person:` / `extra-event:` key for a replayed entity that no golden annotation
covers. A key that a query does not judge is UNJUDGED and never counted as not
relevant: ranking metrics give it no gain (a lower bound) and report how much of
the top k is judged (coverage).

The test split is never run here: rankings, pools and metrics cover dev and validation.
"""

from __future__ import annotations

import json
import math
import time
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from typing import Any, TypedDict

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from db.database import create_database_engine, create_session_factory
from db.maintenance import require_disposable_database
from db.models.persons import PersonRecord
from evaluation.real_world.component_evaluation import IdentityMap, build_identity_map
from evaluation.real_world.db_state import PipelineState, load_pipeline_state
from evaluation.real_world.golden import GoldenDataset, GoldenSplit, load_golden_dataset
from evaluation.real_world.metrics import Span, match_spans
from evaluation.real_world.retrieval_eval import (
    EXTRA_EVENT_PREFIX,
    EXTRA_PERSON_PREFIX,
    RealRetrievalQuery,
)
from semantic_retrieval.document_store import PostgresLexicalEntityRetriever
from semantic_retrieval.embeddings import EmbeddingConfig, SentenceTransformerEmbedder
from semantic_retrieval.models import RetrievalEntityType, RetrievalHit, RetrievalQuery
from semantic_retrieval.relevance import dense_similarity
from semantic_retrieval.retrievers import (
    EntityRetriever,
    HybridEntityRetriever,
    QdrantEntityRetriever,
)

REPORT_DIR = Path("reports/semantic_retrieval_v2")
RUN_DIR = REPORT_DIR / "runs"
POOL_JUDGMENTS_PATH = Path("evaluation/semantic_v2/pool_judgments.json")
SELECTED_THRESHOLDS_PATH = REPORT_DIR / "selected_thresholds.json"
MODELS = ("intfloat/multilingual-e5-base", "BAAI/bge-m3")
BASELINE_MODEL = "intfloat/multilingual-e5-base"
BASELINE_THRESHOLD = 0.80
DEPTH = 100
POOL_DEPTH = 20
RUN_SPLITS = (GoldenSplit.DEV, GoldenSplit.VALIDATION)
CUTOFFS = (5, 10, 20, 100)


def runnable(queries: Iterable[RealRetrievalQuery]) -> list[RealRetrievalQuery]:
    """Only dev and validation queries: the test split is not run at this stage."""
    return [query for query in queries if query.split in RUN_SPLITS]


# --- stand: replayed entities and their judgment keys ------------------------------


@dataclass
class Stand:
    session_factory: sessionmaker[Session]
    golden: GoldenDataset
    state: PipelineState
    identity: IdentityMap
    # (entity type, database id) -> judgment key
    key_of: dict[RetrievalEntityType, dict[int, str]] = field(default_factory=dict)

    def keys(self, entity_type: RetrievalEntityType) -> set[str]:
        return set(self.key_of[entity_type].values())


def person_keys(identity: IdentityMap, names: Mapping[int, str]) -> dict[int, str]:
    keys: dict[int, str] = {}
    used: dict[str, int] = defaultdict(int)
    for person_id in sorted(names):
        golden_ids = identity.golden_of_person.get(person_id, set())
        if golden_ids and person_id not in identity.false_link_persons:
            # Same real person (possibly split by ER into several canonical persons).
            chosen = sorted(golden_ids, key=lambda g: (identity.true_person.get(g) != person_id, g))
            keys[person_id] = chosen[0]
            continue
        if golden_ids:
            label = f"merged({'+'.join(sorted(golden_ids))})"
        else:
            label = names[person_id]
        used[label] += 1
        suffix = f"#{used[label]}" if used[label] > 1 else ""
        keys[person_id] = f"{EXTRA_PERSON_PREFIX}{label}{suffix}"
    return keys


def event_keys(golden: GoldenDataset, state: PipelineState) -> dict[int, str]:
    keys: dict[int, str] = {}
    for article_key, db_events in state.events.items():
        for event in db_events:
            keys[event.event_id] = f"{EXTRA_EVENT_PREFIX}{article_key}@{event.start}-{event.end}"
    for article in golden.articles:
        db_events = state.events.get(article.key, [])
        pairs = match_spans(
            [Span(e.evidence.start, e.evidence.end, e.event_type.value) for e in article.events],
            [Span(e.start, e.end, e.event_type) for e in db_events],
            # Retrieval finds the event by its text: a type the extractor got wrong is
            # still the same event (typing is measured by the extraction benchmark).
            same_label=False,
        )
        for gold_index, db_index in pairs:
            keys[db_events[db_index].event_id] = (
                f"{article.case_id}/{article.events[gold_index].event_id}"
            )
    return keys


def load_stand(database_url: str) -> Stand:
    stand_manifest = json.loads((REPORT_DIR / "stand.json").read_text("utf-8"))
    golden = load_golden_dataset()
    if golden.content_hash() != stand_manifest["golden_dataset_hash"]:
        raise SystemExit("golden dataset changed since the replay: run stand.py replay again")
    engine = create_database_engine(database_url)
    require_disposable_database(engine)
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        state = load_pipeline_state(session, snapshot_id=stand_manifest["snapshot_id"])
        names: dict[int, str] = {
            person_id: name
            for person_id, name in session.execute(
                select(PersonRecord.id, PersonRecord.canonical_name)
            ).all()
        }
    identity = build_identity_map(golden, state)
    return Stand(
        session_factory=session_factory,
        golden=golden,
        state=state,
        identity=identity,
        key_of={
            RetrievalEntityType.PERSON: person_keys(identity, names),
            RetrievalEntityType.EVENT: event_keys(golden, state),
        },
    )


# --- rankings -----------------------------------------------------------------------


@dataclass(frozen=True)
class Ranked:
    key: str
    score: float
    # Cosine similarity of the model's dense retrieval (None: dense did not return it).
    dense: float | None


def retrievers_for(stand: Stand, model_id: str) -> dict[str, EntityRetriever]:
    from evaluation.semantic_v2.stand import collections_for, model_slug, store_for

    embedder = SentenceTransformerEmbedder(EmbeddingConfig(model_id=model_id))
    dense = QdrantEntityRetriever(
        embedder=embedder,
        store=store_for(stand.session_factory, model_id),
        collections=collections_for(model_id),
    )
    lexical = PostgresLexicalEntityRetriever(stand.session_factory)
    slug = model_slug(model_id)
    return {
        f"{slug}_dense": dense,
        f"{slug}_hybrid": HybridEntityRetriever(lexical=lexical, dense=dense),
    }


def rank(
    stand: Stand, hits: Sequence[RetrievalHit], entity_type: RetrievalEntityType
) -> list[Ranked]:
    """Hits -> judgment keys, first occurrence of a key wins (ER split persons share one)."""
    seen: set[str] = set()
    ranked: list[Ranked] = []
    for hit in hits:
        key = stand.key_of[entity_type].get(hit.entity_id, f"#unknown-{hit.entity_id}")
        if key in seen:
            continue
        seen.add(key)
        ranked.append(Ranked(key=key, score=hit.score, dense=dense_similarity(hit)))
    return ranked


def run_system(
    stand: Stand, name: str, retriever: EntityRetriever, queries: Sequence[RealRetrievalQuery]
) -> dict[str, object]:
    rankings: dict[str, list[list[object]]] = {}
    latencies: list[float] = []
    for query in runnable(queries):
        started = time.perf_counter()
        result = retriever.retrieve(
            RetrievalQuery(text=query.text, entity_type=query.entity_type, limit=DEPTH)
        )
        latencies.append((time.perf_counter() - started) * 1000)
        rankings[query.query_id] = [
            [r.key, round(r.score, 6), None if r.dense is None else round(r.dense, 6)]
            for r in rank(stand, result.hits, query.entity_type)
        ]
    ordered = sorted(latencies)
    return {
        "system": name,
        "depth": DEPTH,
        "latency_ms": {
            "p50": round(median(ordered), 2),
            "p95": round(ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)], 2),
            "max": round(ordered[-1], 2),
            "queries": len(ordered),
        },
        "rankings": rankings,
    }


def load_runs() -> dict[str, dict[str, list[Ranked]]]:
    runs: dict[str, dict[str, list[Ranked]]] = {}
    for path in sorted(RUN_DIR.glob("*.json")):
        payload = json.loads(path.read_text("utf-8"))
        runs[payload["system"]] = {
            query_id: [Ranked(key=k, score=s, dense=d) for k, s, d in items]
            for query_id, items in payload["rankings"].items()
        }
    return runs


# --- judgments ----------------------------------------------------------------------


def load_pool_judgments(path: Path = POOL_JUDGMENTS_PATH) -> dict[str, dict[str, int]]:
    """Pool judgments usable by metrics; `cross_split` grades are left out (unjudged)."""
    if not path.exists():
        return {}
    payload = json.loads(path.read_text("utf-8"))
    return {query_id: dict(grades) for query_id, grades in payload["judgments"].items()}


def load_cross_split(path: Path = POOL_JUDGMENTS_PATH) -> dict[str, dict[str, int]]:
    """Relevant grades of entities from another split: kept for review, unjudged in metrics."""
    if not path.exists():
        return {}
    payload = json.loads(path.read_text("utf-8"))
    return {query_id: dict(grades) for query_id, grades in payload.get("cross_split", {}).items()}


def load_retired(path: Path = POOL_JUDGMENTS_PATH) -> dict[str, str]:
    """query -> why pool review took it out of the metrics (e.g. a "negative" with a match)."""
    if not path.exists():
        return {}
    return dict(json.loads(path.read_text("utf-8")).get("retired", {}))


def key_splits(stand: Stand) -> dict[str, set[GoldenSplit | None]]:
    """Judgment key -> splits of the articles its entity comes from (None: not a golden article)."""
    article_split: dict[str, GoldenSplit] = {a.key: a.split for a in stand.golden.articles}
    splits: dict[str, set[GoldenSplit | None]] = defaultdict(set)
    person_key = stand.key_of[RetrievalEntityType.PERSON]
    for article_key, mentions in stand.state.mentions.items():
        for mention in mentions:
            if mention.person_id is not None and mention.person_id in person_key:
                splits[person_key[mention.person_id]].add(article_split.get(article_key))
    event_key = stand.key_of[RetrievalEntityType.EVENT]
    for article_key, events in stand.state.events.items():
        for event in events:
            splits[event_key[event.event_id]].add(article_split.get(article_key))
    return splits


def is_cross_split(query: RealRetrievalQuery, splits: set[GoldenSplit | None]) -> bool:
    """The entity comes only from golden articles of other splits: judging it would leak them."""
    known = {split for split in splits if split is not None}
    return bool(known) and query.split not in known


class PoolJudgments(TypedDict):
    """The committed DRAFT judgment file: grades per query, plus what metrics must skip."""

    status: str
    annotation_origin: str
    note: str
    judgments: dict[str, dict[str, int]]
    cross_split: dict[str, dict[str, int]]
    retired: dict[str, str]


def merge_marks(
    queries: Sequence[RealRetrievalQuery],
    pending: Mapping[str, Mapping[str, object]],
    marks: Mapping[str, Mapping[str, Any]],
    splits: Mapping[str, set[GoldenSplit | None]],
    previous: Mapping[str, Any],
) -> PoolJudgments:
    """DRAFT pool judgments: a pending key the marks do not grade is judged 0 (not relevant).

    A key outside the pool stays unjudged. A relevant key of another split goes to
    `cross_split` and is not used by metrics (unjudged, never negative).
    """
    judgments: dict[str, dict[str, int]] = {
        q: dict(g) for q, g in dict(previous.get("judgments", {})).items()
    }
    cross_split: dict[str, dict[str, int]] = {
        q: dict(g) for q, g in dict(previous.get("cross_split", {})).items()
    }
    retired: dict[str, str] = dict(previous.get("retired", {}))
    by_id = {query.query_id: query for query in queries}
    missing = sorted(set(pending) - set(marks))
    if missing:
        raise SystemExit(f"no marks for pooled queries: {', '.join(missing)}")
    for query_id, mark in marks.items():
        query = by_id[query_id]
        grades = {key: 0 for key in pending.get(query_id, {})}
        grades.update(dict(mark.get("rel", {})))
        for key, grade in grades.items():
            if grade > 0 and is_cross_split(query, splits.get(key, set())):
                cross_split.setdefault(query_id, {})[key] = grade
            else:
                judgments.setdefault(query_id, {})[key] = grade
        if mark.get("retire"):
            retired[query_id] = str(mark["retire"])
    return {
        "status": "DRAFT",
        "annotation_origin": "agent_draft",
        "note": (
            "Agent DRAFT grades of pooled candidates (0/1/2); not verified by a human. "
            "Keys absent here are UNJUDGED, never negative."
        ),
        "judgments": {q: dict(sorted(g.items())) for q, g in sorted(judgments.items())},
        "cross_split": {q: dict(sorted(g.items())) for q, g in sorted(cross_split.items())},
        "retired": dict(sorted(retired.items())),
    }


def all_judgments(
    queries: Sequence[RealRetrievalQuery], pool: Mapping[str, Mapping[str, int]]
) -> dict[str, dict[str, int]]:
    """Query judgments win over pool judgments of the same key."""
    return {q.query_id: {**pool.get(q.query_id, {}), **q.judgments} for q in queries}


# --- ranking metrics ----------------------------------------------------------------


def _dcg(gains: Sequence[int]) -> float:
    return sum((2.0**g - 1.0) / math.log2(i + 1) for i, g in enumerate(gains, 1))


def query_metrics(
    ranking: Sequence[Ranked], judged: Mapping[str, int], reachable: set[str]
) -> dict[str, float] | None:
    """Ranking metrics of one positive query; relevant keys absent from the index are excluded."""
    relevant = {k: g for k, g in judged.items() if g > 0 and k in reachable}
    if not relevant:
        return None
    keys = [r.key for r in ranking]
    out: dict[str, float] = {}
    first = next((i for i, k in enumerate(keys, 1) if k in relevant), None)
    out["mrr"] = 1.0 / first if first else 0.0
    for k in CUTOFFS:
        top = keys[:k]
        out[f"recall@{k}"] = len(set(top) & relevant.keys()) / len(relevant)
        out[f"coverage@{k}"] = sum(key in judged for key in top) / len(top) if top else 1.0
    ideal = sorted(relevant.values(), reverse=True)
    for k in (5, 10):
        top = keys[:k]
        out[f"ndcg@{k}"] = _dcg([judged.get(key, 0) for key in top]) / _dcg(ideal[:k])
        # Condensed list (judged keys only): how much unjudged entities could move nDCG.
        condensed = [key for key in keys if key in judged][:k]
        out[f"ndcg@{k}_condensed"] = _dcg([judged[key] for key in condensed]) / _dcg(ideal[:k])
    return out


def mean_metrics(rows: Sequence[Mapping[str, float]]) -> dict[str, float]:
    if not rows:
        return {}
    return {name: round(sum(r[name] for r in rows) / len(rows), 4) for name in rows[0]}


def categories(query: RealRetrievalQuery) -> list[str]:
    names = ["all", query.entity_type.value]
    tags = set(query.tags)
    if query.semantic_only:
        names.append("semantic_only")
    if "group" in tags:
        names.append("group")
    if tags & {"hard", "namesake_trap", "public_figure_trap"}:
        names.append("hard")
    return names


# --- acceptance (dense similarity threshold) ----------------------------------------


def acceptance(
    queries: Sequence[RealRetrievalQuery],
    rankings: Mapping[str, Sequence[Ranked]],
    judgments: Mapping[str, Mapping[str, int]],
    reachable: Mapping[RetrievalEntityType, set[str]],
    threshold: float,
) -> dict[str, float]:
    """Accept dense top-DEPTH hits with similarity >= threshold (the production policy)."""
    tp = judged_fp = unjudged = relevant_total = 0
    recalls: list[float] = []
    negatives = rejected = hard_fp = offtopic_fp = 0
    for query in queries:
        judged = judgments[query.query_id]
        accepted = [r.key for r in rankings[query.query_id] if (r.dense or -1.0) >= threshold]
        if query.expected_no_match:
            negatives += 1
            rejected += not accepted
            if "negative_hard" in query.tags:
                hard_fp += len(accepted)
            else:
                offtopic_fp += len(accepted)
            continue
        relevant = {k for k, g in judged.items() if g > 0 and k in reachable[query.entity_type]}
        if not relevant:
            continue
        hits = len(relevant.intersection(accepted))
        tp += hits
        relevant_total += len(relevant)
        recalls.append(hits / len(relevant))
        judged_fp += sum(1 for k in accepted if k in judged and judged[k] == 0)
        unjudged += sum(1 for k in accepted if k not in judged)
    accepted_judged = tp + judged_fp
    return {
        "threshold": threshold,
        "recall_macro": round(sum(recalls) / len(recalls), 4) if recalls else 0.0,
        "recall_micro": round(tp / relevant_total, 4) if relevant_total else 0.0,
        "precision_judged": round(tp / accepted_judged, 4) if accepted_judged else 1.0,
        "accepted_unjudged": unjudged,
        "judged_share_of_accepted": (
            round(accepted_judged / (accepted_judged + unjudged), 4)
            if accepted_judged + unjudged
            else 1.0
        ),
        "rejection_rate": round(rejected / negatives, 4) if negatives else 1.0,
        "hard_negative_fp": hard_fp,
        "offtopic_fp": offtopic_fp,
        "negative_queries": negatives,
        "positive_queries": len(recalls),
    }


def sweep(
    queries: Sequence[RealRetrievalQuery],
    rankings: Mapping[str, Sequence[Ranked]],
    judgments: Mapping[str, Mapping[str, int]],
    reachable: Mapping[RetrievalEntityType, set[str]],
    thresholds: Sequence[float],
) -> list[dict[str, float]]:
    return [acceptance(queries, rankings, judgments, reachable, t) for t in thresholds]


def select_threshold(
    rows: Sequence[Mapping[str, float]], baseline: Mapping[str, float]
) -> Mapping[str, float] | None:
    """Pareto rule: max recall with precision, rejection and hard-negative FP no worse than baseline.

    Ties go to the higher (more conservative) threshold.
    """
    feasible = [
        row
        for row in rows
        if row["precision_judged"] >= baseline["precision_judged"]
        and row["rejection_rate"] >= baseline["rejection_rate"]
        and row["hard_negative_fp"] <= baseline["hard_negative_fp"]
    ]
    if not feasible:
        return None
    return max(
        feasible, key=lambda row: (row["recall_micro"], row["recall_macro"], row["threshold"])
    )


def thresholds_grid(start: float = 0.30, stop: float = 0.95, step: float = 0.005) -> list[float]:
    count = round((stop - start) / step)
    return [round(start + i * step, 3) for i in range(count + 1)]


# --- candidate pool -----------------------------------------------------------------

# Band around a threshold, as a share of the model's similarity scale (median similarity
# at rank 1 minus at rank DEPTH): E5 similarities are ~3x more compressed than BGE-M3's.
SENSITIVITY_SHARE = 0.10
DISAGREEMENT_DEPTH = 30


def sensitivity_margins(runs: Mapping[str, Mapping[str, Sequence[Ranked]]]) -> dict[str, float]:
    margins: dict[str, float] = {}
    for system, rankings in runs.items():
        if not system.endswith("_dense"):
            continue
        top = [r[0].dense for r in rankings.values() if r and r[0].dense is not None]
        deep = [
            r[-1].dense for r in rankings.values() if len(r) >= DEPTH and r[-1].dense is not None
        ]
        margins[system.removesuffix("_dense")] = round(
            SENSITIVITY_SHARE * (median(top) - median(deep)), 4
        )
    return margins


def build_pool(
    queries: Sequence[RealRetrievalQuery],
    runs: Mapping[str, Mapping[str, Sequence[Ranked]]],
    judgments: Mapping[str, Mapping[str, int]],
    boundaries: Mapping[str, Sequence[float]],
    margins: Mapping[str, float],
) -> dict[str, dict[str, list[str]]]:
    """query -> key -> reasons it is in the pool (only keys not judged yet).

    - union of the top POOL_DEPTH of every system;
    - deeper: dense similarity within the model's margin of its decision
      threshold (`boundaries`: the E5 baseline first, then the thresholds selected
      on dev — a second pooling stage);
    - deeper: in one model's dense top DISAGREEMENT_DEPTH, absent from the other's top DEPTH.
    Everything else stays UNJUDGED.
    """
    pool: dict[str, dict[str, list[str]]] = {}
    dense_systems = {name: name.removesuffix("_dense") for name in runs if name.endswith("_dense")}
    for query in queries:
        reasons: dict[str, list[str]] = defaultdict(list)
        for system, rankings in runs.items():
            for r in rankings[query.query_id][:POOL_DEPTH]:
                reasons[r.key].append(f"top{POOL_DEPTH}:{system}")
        for system, model in dense_systems.items():
            ranking = runs[system][query.query_id]
            for position, r in enumerate(ranking, 1):
                if position <= POOL_DEPTH or r.dense is None:
                    continue
                for threshold in boundaries.get(model, ()):
                    if abs(r.dense - threshold) <= margins[model]:
                        reasons[r.key].append(f"threshold_sensitive:{model}@{position}~{threshold}")
            for other, other_model in dense_systems.items():
                if other == system:
                    continue
                other_keys = {r.key for r in runs[other][query.query_id]}
                for position, r in enumerate(ranking[:DISAGREEMENT_DEPTH], 1):
                    if position > POOL_DEPTH and r.key not in other_keys:
                        reasons[r.key].append(
                            f"disagreement:{model}@{position},absent_in_{other_model}"
                        )
        judged = judgments[query.query_id]
        pool[query.query_id] = {k: v for k, v in reasons.items() if k not in judged}
    return pool


def document_texts(stand: Stand) -> dict[RetrievalEntityType, dict[str, str]]:
    from db.models.semantic import SemanticDocumentRecord

    texts: dict[RetrievalEntityType, dict[str, str]] = {t: {} for t in RetrievalEntityType}
    with stand.session_factory() as session:
        for entity_type, entity_id, text in session.execute(
            select(
                SemanticDocumentRecord.entity_type,
                SemanticDocumentRecord.entity_id,
                SemanticDocumentRecord.text,
            ).order_by(SemanticDocumentRecord.entity_id)
        ).all():
            kind = RetrievalEntityType(entity_type)
            key = stand.key_of[kind].get(entity_id)
            if key is not None:
                texts[kind].setdefault(key, text)
    return texts


# --- command line -------------------------------------------------------------------


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", "utf-8")


def command_rank(database_url: str, queries: Sequence[RealRetrievalQuery]) -> None:
    stand = load_stand(database_url)
    systems: dict[str, EntityRetriever] = {
        "lexical": PostgresLexicalEntityRetriever(stand.session_factory)
    }
    for model_id in MODELS:
        systems.update(retrievers_for(stand, model_id))
    for name, retriever in systems.items():
        # Warm-up: model load and first query are not latency.
        retriever.retrieve(
            RetrievalQuery(text="прогрев", entity_type=RetrievalEntityType.PERSON, limit=DEPTH)
        )
        payload = run_system(stand, name, retriever, queries)
        _write_json(RUN_DIR / f"{name}.json", payload)
        print(name, payload["latency_ms"])


def reachable_keys(stand: Stand) -> dict[RetrievalEntityType, set[str]]:
    return {entity_type: stand.keys(entity_type) for entity_type in RetrievalEntityType}


def command_pool(database_url: str, queries: Sequence[RealRetrievalQuery], sheet: Path) -> None:
    stand = load_stand(database_url)
    runs = load_runs()
    queries = runnable(queries)
    judgments = all_judgments(queries, load_pool_judgments())
    # Cross-split grades are not used by metrics, but they were judged: not pooled again.
    for query_id, grades in load_cross_split().items():
        judgments[query_id] = {**grades, **judgments.get(query_id, {})}
    boundaries: dict[str, list[float]] = {"e5": [BASELINE_THRESHOLD]}
    if SELECTED_THRESHOLDS_PATH.exists():
        # Second stage: the thresholds selected on dev.
        for model, threshold in json.loads(SELECTED_THRESHOLDS_PATH.read_text("utf-8")).items():
            if threshold not in boundaries.setdefault(model, []):
                boundaries[model].append(threshold)
    margins = sensitivity_margins(runs)
    pool = build_pool(queries, runs, judgments, boundaries, margins)
    texts = document_texts(stand)
    _write_json(
        REPORT_DIR / "pool.json",
        {
            "rules": {
                "top_k_union": POOL_DEPTH,
                "systems": sorted(runs),
                "threshold_boundaries": boundaries,
                "sensitivity_margins": margins,
                "disagreement_depth": DISAGREEMENT_DEPTH,
            },
            "pending": {qid: keys for qid, keys in pool.items() if keys},
            "pending_pairs": sum(len(keys) for keys in pool.values()),
        },
    )
    lines: list[str] = []
    for query in queries:
        pending = pool[query.query_id]
        if not pending:
            continue
        lines.append(f"### {query.query_id} [{query.entity_type.value}] {query.text}")
        known = judgments[query.query_id]
        if known:
            lines.append("  judged: " + ", ".join(f"{k}={g}" for k, g in known.items()))
        best = {
            key: min(
                (
                    i
                    for system in runs.values()
                    for i, r in enumerate(system[query.query_id], 1)
                    if r.key == key
                ),
                default=DEPTH + 1,
            )
            for key in pending
        }
        for key in sorted(pending, key=lambda k: (best[k], k)):
            text = texts[query.entity_type].get(key, "")
            head = [
                line.strip()
                for line in text.splitlines()
                if not line.startswith(
                    ("Классификация", "Другие написания", "Упоминания:", "События:", "Источник:")
                )
            ]
            lines.append(f"  {key} :: {' '.join(head[:3])[:110]}")
    sheet.write_text("\n".join(lines) + "\n", "utf-8")
    print(
        f"boundaries={boundaries} pending_pairs={sum(len(k) for k in pool.values())} sheet={sheet}"
    )


def command_judge(
    database_url: str, queries: Sequence[RealRetrievalQuery], marks_path: Path
) -> None:
    """Merge DRAFT marks (JSON lines {"q", "rel", "retire"?}; the last line of a query wins)."""
    stand = load_stand(database_url)
    marks: dict[str, dict[str, object]] = {}
    for line in marks_path.read_text("utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            marks[record["q"]] = record
    pending = json.loads((REPORT_DIR / "pool.json").read_text("utf-8"))["pending"]
    previous = (
        json.loads(POOL_JUDGMENTS_PATH.read_text("utf-8")) if POOL_JUDGMENTS_PATH.exists() else {}
    )
    payload = merge_marks(runnable(queries), pending, marks, key_splits(stand), previous)
    _write_json(POOL_JUDGMENTS_PATH, payload)
    judged = payload["judgments"]
    print(
        f"queries={len(judged)} pairs={sum(len(g) for g in judged.values())} "
        f"relevant={sum(1 for g in judged.values() for v in g.values() if v > 0)} "
        f"cross_split={sum(len(g) for g in payload['cross_split'].values())} "
        f"retired={sorted(payload['retired'])}"
    )


def _percentiles(values: Sequence[float]) -> dict[str, float]:
    if not values:
        return {"n": 0}
    ordered = sorted(values)

    def at(share: float) -> float:
        return round(ordered[min(len(ordered) - 1, int(share * len(ordered)))], 4)

    return {
        "n": len(ordered),
        "p05": at(0.05),
        "p25": at(0.25),
        "p50": at(0.5),
        "p75": at(0.75),
        "p95": at(0.95),
        "max": round(ordered[-1], 4),
    }


def similarity_distribution(
    queries: Sequence[RealRetrievalQuery],
    rankings: Mapping[str, Sequence[Ranked]],
    judgments: Mapping[str, Mapping[str, int]],
) -> dict[str, object]:
    """Dense similarity by rank and by judgment: where relevant and non-relevant hits sit."""
    by_rank: dict[int, list[float]] = defaultdict(list)
    relevant: list[float] = []
    judged_zero: list[float] = []
    negative_top1: dict[str, list[float]] = defaultdict(list)
    for query in queries:
        ranking = rankings[query.query_id]
        for position in (1, 5, 20, 100):
            if len(ranking) >= position and ranking[position - 1].dense is not None:
                by_rank[position].append(ranking[position - 1].dense)  # type: ignore[arg-type]
        if query.expected_no_match:
            kind = "hard" if "negative_hard" in query.tags else "offtopic"
            if ranking and ranking[0].dense is not None:
                negative_top1[kind].append(ranking[0].dense)
            continue
        judged = judgments[query.query_id]
        for r in ranking:
            if r.dense is None or r.key not in judged:
                continue
            (relevant if judged[r.key] > 0 else judged_zero).append(r.dense)
    return {
        "by_rank": {f"rank{p}": _percentiles(v) for p, v in sorted(by_rank.items())},
        "judged_relevant": _percentiles(relevant),
        "judged_not_relevant": _percentiles(judged_zero),
        "negative_query_top1": {k: _percentiles(v) for k, v in sorted(negative_top1.items())},
    }


def ranking_report(
    queries: Sequence[RealRetrievalQuery],
    runs: Mapping[str, Mapping[str, Sequence[Ranked]]],
    judgments: Mapping[str, Mapping[str, int]],
    reachable: Mapping[RetrievalEntityType, set[str]],
) -> dict[str, object]:
    """system -> category -> mean metrics over positive queries with a reachable relevant key."""
    report: dict[str, object] = {}
    skipped = sorted(
        q.query_id
        for q in queries
        if not q.expected_no_match
        and not any(
            g > 0 and k in reachable[q.entity_type] for k, g in judgments[q.query_id].items()
        )
    )
    for system, rankings in sorted(runs.items()):
        rows: dict[str, list[dict[str, float]]] = defaultdict(list)
        for query in queries:
            if query.expected_no_match:
                continue
            row = query_metrics(
                rankings[query.query_id], judgments[query.query_id], reachable[query.entity_type]
            )
            if row is None:
                continue
            for category in categories(query):
                rows[category].append(row)
        report[system] = {
            category: {"queries": len(r), **mean_metrics(r)} for category, r in sorted(rows.items())
        }
    return {"no_reachable_relevant": skipped, "systems": report}


BOOTSTRAP_SAMPLES = 2000
PAIRS = (("bge_m3_dense", "e5_dense"), ("bge_m3_hybrid", "e5_hybrid"))


def paired_bootstrap(
    queries: Sequence[RealRetrievalQuery],
    runs: Mapping[str, Mapping[str, Sequence[Ranked]]],
    judgments: Mapping[str, Mapping[str, int]],
    reachable: Mapping[RetrievalEntityType, set[str]],
    metrics: Sequence[str] = ("mrr", "recall@10", "ndcg@10"),
) -> dict[str, dict[str, dict[str, float]]]:
    """Mean per-query difference (a - b) and its 95% percentile interval, seeded."""
    import random

    out: dict[str, dict[str, dict[str, float]]] = {}
    for a, b in PAIRS:
        diffs: dict[str, list[float]] = defaultdict(list)
        for query in queries:
            if query.expected_no_match:
                continue
            judged, keys = judgments[query.query_id], reachable[query.entity_type]
            row_a = query_metrics(runs[a][query.query_id], judged, keys)
            row_b = query_metrics(runs[b][query.query_id], judged, keys)
            if row_a is None or row_b is None:
                continue
            for name in metrics:
                diffs[name].append(row_a[name] - row_b[name])
        rng = random.Random(0)
        result: dict[str, dict[str, float]] = {}
        for name, values in diffs.items():
            n = len(values)
            means = sorted(
                sum(values[rng.randrange(n)] for _ in range(n)) / n
                for _ in range(BOOTSTRAP_SAMPLES)
            )
            result[name] = {
                "mean_diff": round(sum(values) / n, 4),
                "ci95_low": round(means[int(0.025 * BOOTSTRAP_SAMPLES)], 4),
                "ci95_high": round(means[int(0.975 * BOOTSTRAP_SAMPLES) - 1], 4),
                "queries": n,
            }
        out[f"{a} - {b}"] = result
    return out


def command_evaluate(database_url: str, queries: Sequence[RealRetrievalQuery]) -> None:
    """Dev: ranking metrics, similarity, threshold sweep and selection (frozen to a file).

    Validation: the same ranking metrics, and acceptance at the frozen thresholds only.
    """
    stand = load_stand(database_url)
    reachable = reachable_keys(stand)
    runs = load_runs()
    retired = load_retired()
    active = [q for q in runnable(queries) if q.query_id not in retired]
    judgments = all_judgments(active, load_pool_judgments())
    by_split = {split: [q for q in active if q.split is split] for split in RUN_SPLITS}
    models = [s.removesuffix("_dense") for s in sorted(runs) if s.endswith("_dense")]
    baseline_model = "e5"
    grid = thresholds_grid()

    def report_for(split: GoldenSplit) -> dict[str, object]:
        split_queries = by_split[split]
        return {
            "split": split.value,
            "status": "PRELIMINARY (DRAFT judgments)",
            "queries": len(split_queries),
            "negative_queries": sum(q.expected_no_match for q in split_queries),
            "ranking": ranking_report(split_queries, runs, judgments, reachable),
            "paired_bootstrap": paired_bootstrap(split_queries, runs, judgments, reachable),
            "judgment_coverage": {
                "note": "coverage@k in ranking = judged share of the top k; "
                "recall@100 is a lower bound (unjudged entities count as not found)",
            },
            "similarity": {
                m: similarity_distribution(split_queries, runs[f"{m}_dense"], judgments)
                for m in models
            },
        }

    dev = by_split[GoldenSplit.DEV]
    baseline = acceptance(
        dev, runs[f"{baseline_model}_dense"], judgments, reachable, BASELINE_THRESHOLD
    )
    selected: dict[str, float] = {}
    sweeps: dict[str, object] = {}
    for model in models:
        rows = sweep(dev, runs[f"{model}_dense"], judgments, reachable, grid)
        choice = select_threshold(rows, baseline)
        sweeps[model] = {"selected": choice, "rows": rows}
        if choice is not None:
            selected[model] = choice["threshold"]
    dev_report = report_for(GoldenSplit.DEV)
    dev_report["acceptance"] = {"baseline": baseline, "sweeps": sweeps}
    _write_json(REPORT_DIR / "dev.json", dev_report)
    _write_json(SELECTED_THRESHOLDS_PATH, selected)

    validation = by_split[GoldenSplit.VALIDATION]
    validation_report = report_for(GoldenSplit.VALIDATION)
    frozen = {f"{baseline_model}@{BASELINE_THRESHOLD}": (baseline_model, BASELINE_THRESHOLD)}
    frozen.update({f"{m}@{t} (dev-selected)": (m, t) for m, t in selected.items()})
    validation_report["acceptance"] = {
        name: {
            system: acceptance(validation, runs[f"{model}_{system}"], judgments, reachable, t)
            for system in ("dense", "hybrid")
        }
        for name, (model, t) in frozen.items()
    }
    _write_json(REPORT_DIR / "validation.json", validation_report)
    print(f"selected={selected} baseline(dev)={baseline}")


REVIEW_DIR = Path("var/real_world/review/semantic_v2")
REVIEW_PRIORITY_DEPTH = 5


def command_review(database_url: str, queries: Sequence[RealRetrievalQuery]) -> None:
    """Blind human review sheets: no system, rank, score or agent grade in the sheet.

    `sheet_priority.csv`: pairs in any system's top 5 (they decide MRR, nDCG@5 and
    the head of every report; a threshold band is too wide on E5's compressed scale
    to be a priority); `sheet_full.csv`: every judged pair. Rows are shuffled with a fixed seed; `keys.json` maps the
    opaque item id back to (query, key) for reconciliation with the DRAFT grades.
    """
    import csv
    import hashlib
    import random

    stand = load_stand(database_url)
    runs = load_runs()
    retired = load_retired()
    active = [q for q in runnable(queries) if q.query_id not in retired]
    judgments = all_judgments(active, load_pool_judgments())
    for query_id, grades in load_cross_split().items():
        judgments[query_id] = {**grades, **judgments.get(query_id, {})}
    texts = document_texts(stand)
    items: list[dict[str, str]] = []
    priority: set[str] = set()
    for query in active:
        for key in judgments[query.query_id]:
            item_id = hashlib.sha256(f"{query.query_id}|{key}".encode()).hexdigest()[:12]
            items.append(
                {
                    "item_id": item_id,
                    "query": query.text,
                    "entity_type": query.entity_type.value,
                    "no_match_expected": "yes" if query.expected_no_match else "",
                    "document": texts[query.entity_type].get(key, "(document not found)"),
                    "grade_0_1_2": "",
                    "comment": "",
                    "_query_id": query.query_id,
                    "_key": key,
                }
            )
            if any(
                key in {r.key for r in rankings[query.query_id][:REVIEW_PRIORITY_DEPTH]}
                for rankings in runs.values()
            ):
                priority.add(item_id)
    random.Random(0).shuffle(items)
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    columns = [
        "item_id",
        "query",
        "entity_type",
        "no_match_expected",
        "document",
        "grade_0_1_2",
        "comment",
    ]
    for name, rows in (
        ("sheet_priority.csv", [i for i in items if i["item_id"] in priority]),
        ("sheet_full.csv", items),
    ):
        with (REVIEW_DIR / name).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    _write_json(
        REVIEW_DIR / "keys.json",
        {i["item_id"]: [i["_query_id"], i["_key"]] for i in items},
    )
    (REVIEW_DIR / "README.md").write_text(
        "# Semantic Retrieval v2 — blind relevance review\n\n"
        "Grade how well the document answers the query: 2 = exactly the searched entity,\n"
        "1 = partially / related to the same case, 0 = not relevant. For a query marked\n"
        "`no_match_expected`, any grade above 0 means the query is not a clean no-match.\n"
        "The sheet shows no system, rank, score or earlier grade on purpose.\n"
        "Start with sheet_priority.csv (pairs in some system's top 5); sheet_full.csv has\n"
        "every judged pair. keys.json maps item_id to (query id, entity key).\n",
        "utf-8",
    )
    print(f"items={len(items)} priority={len(priority)} -> {REVIEW_DIR}")


def main() -> None:
    import argparse

    from evaluation.real_world.retrieval_eval import load_retrieval_queries

    parser = argparse.ArgumentParser(description="Semantic Retrieval v2 evaluation")
    parser.add_argument("command", choices=["rank", "pool", "judge", "evaluate", "review"])
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--sheet", type=Path, default=Path("pool_sheet.txt"))
    parser.add_argument("--marks", type=Path)
    args = parser.parse_args()
    queries = load_retrieval_queries()
    if args.command == "rank":
        command_rank(args.database_url, queries)
    elif args.command == "pool":
        command_pool(args.database_url, queries, args.sheet)
    elif args.command == "judge":
        command_judge(args.database_url, queries, args.marks)
    elif args.command == "evaluate":
        command_evaluate(args.database_url, queries)
    else:
        command_review(args.database_url, queries)


def lexemes(session_factory: sessionmaker[Session], text: str) -> set[str]:
    """Stems of `text` under the lexical backend's configuration (russian, stop words removed)."""
    from sqlalchemy import func

    with session_factory() as session:
        return set(
            session.scalar(select(func.tsvector_to_array(func.to_tsvector("russian", text)))) or []
        )


def semantic_only_overlaps(
    stand: Stand,
    queries: Sequence[RealRetrievalQuery],
    texts: Mapping[RetrievalEntityType, Mapping[str, str]],
) -> dict[str, dict[str, list[str]]]:
    """query -> relevant key -> stems shared by the query and that key's document."""
    overlaps: dict[str, dict[str, list[str]]] = {}
    for query in queries:
        if not query.semantic_only:
            continue
        query_stems = lexemes(stand.session_factory, query.text)
        overlaps[query.query_id] = {
            key: sorted(query_stems & lexemes(stand.session_factory, texts[query.entity_type][key]))
            for key in query.relevant
            if key in texts[query.entity_type]
        }
    return overlaps


if __name__ == "__main__":
    main()
