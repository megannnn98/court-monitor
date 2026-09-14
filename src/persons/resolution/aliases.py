"""Which linked surface forms become canonical aliases of a Person.

An alias is reused as identity evidence for every later mention, so only clean
full forms qualify: no initials, no typo-tolerated component, no incomplete or
unparsed name. Other forms still link; they just do not become aliases.
"""

from __future__ import annotations

from persons.resolution.models import ComponentMatch, PersonResolutionFeatures
from persons.resolution.normalizer import PersonNameNormalizer

_CLEAN = {ComponentMatch.EXACT, ComponentMatch.MISSING}


class AliasPromotionPolicy:
    def __init__(self, normalizer: PersonNameNormalizer | None = None) -> None:
        self._normalizer = normalizer or PersonNameNormalizer()

    def should_promote(self, name: str, features: PersonResolutionFeatures) -> bool:
        normalized = self._normalizer.normalize(name)
        return (
            not normalized.has_initials
            and len(normalized.full_tokens) >= 2
            and bool(normalized.variants)
            and not features.conflicts
            and not features.incomplete_name
            and features.surname is ComponentMatch.EXACT
            and features.given_name is ComponentMatch.EXACT
            and features.patronymic in _CLEAN
        )
