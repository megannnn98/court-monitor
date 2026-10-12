"""Evidence as the result holds it: no repeats, ranked, capped, a number to cite it by.

Repeats. One quote is one piece of evidence however many retrievers or rounds found it
(`EvidenceItem.identity`): its place in its article, a post's id at its source. Of one
document, a second quote that tells nothing the first does not — same person, looked
up for the same facts — is a repeat too; two quotes of one document for different
facts, or of different people, both stay.

Ranking. What the pipeline decided comes before what a text says, so a ceiling never
cuts a fact to keep a quote. Quotes tied to the person come before quotes that only
hold the name. Within that, a quote ranks by Reciprocal Rank Fusion of its places in
the lists that found it — scores of different retrievers never compare (a ts_rank and
a source's own order), ranks do — and by recency last.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from investigation.models import EvidenceItem

# The k of Reciprocal Rank Fusion (Cormack et al., 2009): 1 / (k + rank).
RRF_K = 60


def _same_place(found: Sequence[EvidenceItem]) -> dict[tuple[object, ...], EvidenceItem]:
    unique: dict[tuple[object, ...], EvidenceItem] = {}
    for item in found:
        place = item.identity
        held = unique.setdefault(place, item)
        if held is item:
            continue
        if held.entity_key and item.entity_key and held.entity_key != item.entity_key:
            # One place in a text tied to two people is evidence of each, not of
            # whoever came first.
            place = (*place, item.entity_key)
            held = unique.setdefault(place, item)
        if held is not item:
            # The same quote found twice — by two sources, or again while looking for a
            # missing fact — is one piece of evidence; it keeps whose person it is and
            # every fact it was looked up for.
            unique[place] = held.model_copy(
                update={
                    "entity_key": held.entity_key or item.entity_key,
                    "leads": tuple(dict.fromkeys((*held.leads, *item.leads))),
                }
            )
    return unique


def rrf_scores(found: Sequence[EvidenceItem]) -> dict[tuple[object, ...], float]:
    """Each document's fused rank over the result lists it stood in: one list per
    retriever and query, in the order the retriever returned it."""
    positions: dict[tuple[str, str | None, int | None], int] = defaultdict(int)
    scores: dict[tuple[object, ...], float] = defaultdict(float)
    seen: set[tuple[object, ...]] = set()
    for item in found:
        document = item.document
        if document is None:
            continue
        listed = (item.source_id, item.query, item.round)
        positions[listed] += 1
        once = (*listed, *document)
        if once in seen:
            continue
        seen.add(once)
        scores[document] += 1.0 / (RRF_K + positions[listed])
    return dict(scores)


def merge(found: Sequence[EvidenceItem], *, limit: int) -> tuple[list[EvidenceItem], int]:
    """`found` without repeats, ranked, numbered «E1», «E2», …; and how many the
    ceiling cut."""
    unique = list(_same_place(found).values())
    fused = rrf_scores(found)

    # Of one document, one quote per (person, facts looked up for).
    kept: dict[tuple[object, ...], EvidenceItem] = {}
    owners: dict[tuple[object, ...], set[str]] = defaultdict(set)
    for item in unique:
        if item.document is not None and item.entity_key:
            owners[item.document].add(item.entity_key)
    for item in unique:
        document = item.document
        if document is None:
            kept[("fact", *item.identity)] = item
            continue
        if item.entity_key is None and owners[document] and not item.leads:
            # A quote of a text that only holds the name, of a document already quoted
            # as the person's own: the person's quote says more.
            continue
        key = (document, item.entity_key, item.leads)
        kept.setdefault(key, item)

    ordered = sorted(kept.values(), key=lambda item: _rank(item, fused))
    shown = ordered[:limit]
    return (
        [item.model_copy(update={"ref": f"E{number}"}) for number, item in enumerate(shown, 1)],
        len(ordered) - len(shown),
    )


def _rank(
    item: EvidenceItem, fused: dict[tuple[object, ...], float]
) -> tuple[bool, bool, float, float]:
    """Facts first, in the order they came; then the person's quotes before those that
    only hold the name; then the fused rank; then the newest."""
    document = item.document
    if document is None:
        return (False, False, 0.0, 0.0)
    newest = -item.published_at.timestamp() if item.published_at else float("inf")
    return (True, item.entity_key is None, -fused.get(document, 0.0), newest)
