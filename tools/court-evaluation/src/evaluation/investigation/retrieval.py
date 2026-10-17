"""Which retriever finds a person's publications: structured, by name in the text,
ranked full-text.

Each is asked the golden set's queries and ranks articles; the relevant ones are the
publications whose mentions made the person's entity group. That makes `structured`
an upper bound — its answer is read from the same mentions — and the others the real
comparison: how well a text search finds what the pipeline tied to the person.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from evaluation.investigation.golden import GoldenSet, PersonCase
from evaluation.investigation.metrics import mean, ndcg_at, recall_at, reciprocal_rank
from results import dossier, search

DEPTH = 20

Ranker = Callable[[str], list[int]]


@dataclass(frozen=True)
class Scores:
    recall_5: float
    recall_10: float
    mrr: float
    ndcg_10: float
    cases: int


def score(cases: Sequence[PersonCase], ranker: Ranker) -> Scores:
    recalls_5, recalls_10, mrrs, ndcgs = [], [], [], []
    for case in cases:
        ranked = ranker(case.query)
        relevant = set(case.relevant_articles)
        recalls_5.append(recall_at(ranked, relevant, 5))
        recalls_10.append(recall_at(ranked, relevant, 10))
        mrrs.append(reciprocal_rank(ranked, relevant))
        ndcgs.append(ndcg_at(ranked, relevant, 10))
    return Scores(
        recall_5=round(mean(recalls_5), 3),
        recall_10=round(mean(recalls_10), 3),
        mrr=round(mean(mrrs), 3),
        ndcg_10=round(mean(ndcgs), 3),
        cases=len(cases),
    )


def structured(session: Session) -> Ranker:
    def rank(query: str) -> list[int]:
        hits = dossier.find_entities(session, query, limit=5)
        if not hits:
            return []
        quotes = dossier.entity_evidence(session, hits[0].key, limit=DEPTH * 3)
        return list(dict.fromkeys(quote.article_id for quote in quotes))[:DEPTH]

    return rank


def stored_like(session: Session) -> Ranker:
    def rank(query: str) -> list[int]:
        quotes = dossier.stored_publication_quotes(session, query, limit=DEPTH)
        return [quote.article_id for quote in quotes]

    return rank


def lexical(session: Session) -> Ranker:
    def rank(query: str) -> list[int]:
        return [
            item.article_id for item in search.search_publications(session, name=query, limit=DEPTH)
        ]

    return rank


def compare(session: Session, golden: GoldenSet) -> dict[str, Any]:
    rankers: dict[str, Ranker] = {
        "structured (upper bound)": structured(session),
        "stored_publications (LIKE)": stored_like(session),
        "publications_fts (lexical)": lexical(session),
    }
    by_form: dict[str, list[PersonCase]] = defaultdict(list)
    for case in golden.persons:
        by_form[case.form].append(case)
    table: dict[str, Any] = {}
    for name, ranker in rankers.items():
        table[name] = {
            "all": score(golden.persons, ranker).__dict__,
            **{form: score(cases, ranker).__dict__ for form, cases in sorted(by_form.items())},
        }
    return table


def as_markdown(table: Mapping[str, Mapping[str, Mapping[str, float]]]) -> str:
    lines = [
        "| retriever | form | Recall@5 | Recall@10 | MRR | NDCG@10 | cases |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, forms in table.items():
        for form, values in forms.items():
            lines.append(
                f"| {name} | {form} | {values['recall_5']} | {values['recall_10']} | "
                f"{values['mrr']} | {values['ndcg_10']} | {values['cases']} |"
            )
    return "\n".join(lines)
