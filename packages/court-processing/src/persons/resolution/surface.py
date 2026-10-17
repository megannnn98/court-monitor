"""The identity the extraction pipeline hands to ER v2 for a surface form."""

from __future__ import annotations

from persons.resolution.models import PersonIdentityInput
from processing.extraction.normalizers import RuleBasedMentionNormalizer

_extraction_normalizer = RuleBasedMentionNormalizer()


def pipeline_identity(surface: str) -> PersonIdentityInput:
    """The identity the extraction pipeline would hand to ER for this surface form."""
    _, data = _extraction_normalizer.normalize_person(surface)
    return PersonIdentityInput(
        name=data.full_name, surface_text=surface, matching_key=data.matching_key
    )
