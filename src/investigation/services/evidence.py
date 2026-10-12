"""Evidence as the result holds it: no repeats, a ceiling, a number to cite it by."""

from __future__ import annotations

from collections.abc import Sequence

from investigation.models import EvidenceItem, Subject, SubjectCandidate


def merge(found: Sequence[EvidenceItem], *, limit: int) -> tuple[list[EvidenceItem], int]:
    """`found` without repeats, numbered «E1», «E2», … in the order it came; and how many
    the ceiling cut. What the pipeline decided comes before what a publication says, so
    a ceiling never cuts a fact to keep a quote."""
    unique: dict[tuple[object, ...], EvidenceItem] = {}
    for item in found:
        held = unique.setdefault(item.identity, item)
        if held is not item:
            # The same quote found twice — by two sources, or again while looking for a
            # missing fact — is one piece of evidence; it keeps whose person it is and
            # what it was looked up for.
            unique[item.identity] = held.model_copy(
                update={
                    "entity_key": held.entity_key or item.entity_key,
                    "lead": held.lead or item.lead,
                }
            )
    ordered = sorted(unique.values(), key=lambda item: item.fact is None)
    kept = ordered[:limit]
    return (
        [item.model_copy(update={"ref": f"E{number}"}) for number, item in enumerate(kept, 1)],
        len(ordered) - len(kept),
    )


def resolve_subject(name: str, evidence: Sequence[EvidenceItem]) -> Subject:
    """The person asked about. One entity group answering the name is that person;
    several are candidates a human tells apart; none leaves the key unset."""
    candidates: dict[str, SubjectCandidate] = {}
    for item in evidence:
        if item.fact == "identity" and item.entity_key is not None:
            candidates.setdefault(
                item.entity_key,
                SubjectCandidate(
                    entity_key=item.entity_key,
                    name=item.value or item.entity_key,
                    description=item.text,
                ),
            )
    found = list(candidates.values())
    return Subject(
        name=name,
        entity_key=found[0].entity_key if len(found) == 1 else None,
        candidates=found,
    )
