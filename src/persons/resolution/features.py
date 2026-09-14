"""Feature extraction: compare an incoming name with one candidate person.

Features only describe; they decide nothing. Scoring and the decision policy
live in `scoring.py` and `decision.py`.
"""

from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein

from persons.resolution.models import (
    ComponentMatch,
    IdentityConflict,
    NameComponent,
    NameRole,
    NameVariant,
    NormalizedPersonName,
    PersonIdentityInput,
    PersonResolutionCandidate,
    PersonResolutionFeatures,
)
from persons.resolution.normalizer import PersonNameNormalizer

_MATCH_WEIGHT = {
    ComponentMatch.EXACT: 3,
    ComponentMatch.TYPO: 2,
    ComponentMatch.INITIAL_COMPATIBLE: 1,
    ComponentMatch.MISSING: 0,
    ComponentMatch.MISMATCH: 0,
}
_CONFLICT_BY_ROLE = {
    NameRole.SURNAME: IdentityConflict.SURNAME_MISMATCH,
    NameRole.GIVEN_NAME: IdentityConflict.GIVEN_NAME_MISMATCH,
    NameRole.PATRONYMIC: IdentityConflict.PATRONYMIC_MISMATCH,
}


def typo_tolerance(length: int) -> int:
    """Edits accepted as a typo: none for short names ("Иван"/"Иона" differ)."""
    if length >= 10:
        return 2
    if length >= 5:
        return 1
    return 0


def compare_components(
    left: NameComponent | None, right: NameComponent | None
) -> tuple[ComponentMatch, float | None]:
    if left is None or right is None:
        return ComponentMatch.MISSING, None
    if left.is_initial or right.is_initial:
        compatible = left.text[0] == right.text[0]
        return (ComponentMatch.INITIAL_COMPATIBLE if compatible else ComponentMatch.MISMATCH), None
    if left.text == right.text:
        return ComponentMatch.EXACT, 1.0
    similarity = Levenshtein.normalized_similarity(left.text, right.text)
    distance = Levenshtein.distance(left.text, right.text)
    # By the longer form: a dropped letter in a short name ("Смитт"/"Смит") is still a typo.
    if distance <= typo_tolerance(max(len(left.text), len(right.text))):
        return ComponentMatch.TYPO, similarity
    return ComponentMatch.MISMATCH, similarity


@dataclass(frozen=True)
class _Alignment:
    matches: dict[NameRole, tuple[ComponentMatch, float | None]]
    order_differs: bool
    plausibility: int
    incoming: NameVariant | None

    @property
    def conflicts(self) -> list[IdentityConflict]:
        return [
            _CONFLICT_BY_ROLE[role]
            for role in NameRole
            if self.matches[role][0] is ComponentMatch.MISMATCH
        ]

    @property
    def rank(self) -> tuple[int, int, int, bool]:
        # Conflicts do not rank first: an implausible reading must not hide them.
        weight = sum(_MATCH_WEIGHT[match] for match, _ in self.matches.values())
        return (weight, self.plausibility, -len(self.conflicts), not self.order_differs)


def _align(incoming: NameVariant, candidate: NameVariant) -> _Alignment:
    return _Alignment(
        matches={
            role: compare_components(incoming.component(role), candidate.component(role))
            for role in NameRole
        },
        order_differs=_shared_order(incoming, candidate) != _shared_order(candidate, incoming),
        plausibility=incoming.plausibility + candidate.plausibility,
        incoming=incoming,
    )


def _shared_order(variant: NameVariant, other: NameVariant) -> tuple[NameRole, ...]:
    """Order of the roles both readings have: a missing part is not a reordering."""
    present = {part.role for part in other.components}
    return tuple(part.role for part in variant.components if part.role in present)


_UNALIGNED = _Alignment(
    matches={role: (ComponentMatch.MISSING, None) for role in NameRole},
    order_differs=False,
    plausibility=0,
    incoming=None,
)


def _best_alignment(incoming: NormalizedPersonName, form: NormalizedPersonName) -> _Alignment:
    alignments = [_align(left, right) for left in incoming.variants for right in form.variants]
    return max(alignments, key=lambda alignment: alignment.rank, default=_UNALIGNED)


class PersonResolutionFeatureExtractor:
    def __init__(self, normalizer: PersonNameNormalizer | None = None) -> None:
        self._normalizer = normalizer or PersonNameNormalizer()

    def extract(
        self, identity: PersonIdentityInput, candidate: PersonResolutionCandidate
    ) -> PersonResolutionFeatures:
        incoming = self._normalizer.normalize(identity.name)
        canonical = self._normalizer.normalize(candidate.canonical_name)
        aliases = [self._normalizer.normalize(alias) for alias in candidate.aliases]

        # Canonical name first: on equal rank the canonical form wins.
        forms = [(canonical, False), *((alias, True) for alias in aliases)]
        scored = [(_best_alignment(incoming, form), form, is_alias) for form, is_alias in forms]
        alignment, form, is_alias = max(scored, key=lambda item: item[0].rank)

        given = alignment.incoming.component(NameRole.GIVEN_NAME) if alignment.incoming else None
        return PersonResolutionFeatures(
            compared_form=form.canonical_form,
            compared_form_is_alias=is_alias,
            exact_matching_key=identity.matching_key is not None
            and identity.matching_key == candidate.matching_key,
            exact_name=incoming.canonical_form == canonical.canonical_form,
            exact_alias=any(incoming.canonical_form == alias.canonical_form for alias in aliases),
            surname=alignment.matches[NameRole.SURNAME][0],
            given_name=alignment.matches[NameRole.GIVEN_NAME][0],
            patronymic=alignment.matches[NameRole.PATRONYMIC][0],
            surname_similarity=alignment.matches[NameRole.SURNAME][1],
            given_name_similarity=alignment.matches[NameRole.GIVEN_NAME][1],
            patronymic_similarity=alignment.matches[NameRole.PATRONYMIC][1],
            full_name_similarity=fuzz.ratio(incoming.canonical_form, form.canonical_form) / 100,
            order_differs=alignment.order_differs,
            initials_only=given is not None and given.is_initial,
            incomplete_name=incoming.is_incomplete
            or (alignment.incoming is not None and given is None),
            conflicts=alignment.conflicts,
            semantic_similarity=candidate.semantic_similarity,
        )
