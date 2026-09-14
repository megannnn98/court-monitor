"""Rule-based resolution score.

`resolution_score` is an ordinal evidence score for ranking and thresholds, not
a calibrated probability that two names belong to one person.

Evidence strength (ADR 0012):
- strong: exact full name / exact known alias (surname + given name + patronymic);
- medium: reordered full name, a minor typo in one component;
- weak: surname alone, initials, semantic similarity — weak evidence alone stays
  below any sensible auto-link threshold, and semantic similarity adds nothing.
A conflicting component caps the score instead of being averaged away.
"""

from __future__ import annotations

from persons.resolution.models import (
    ComponentMatch,
    PersonResolutionFeatures,
    PersonResolutionScore,
)

_SURNAME = {ComponentMatch.EXACT: 0.45, ComponentMatch.TYPO: 0.30}
_GIVEN_NAME = {
    ComponentMatch.EXACT: 0.30,
    ComponentMatch.TYPO: 0.20,
    ComponentMatch.INITIAL_COMPATIBLE: 0.08,
}
_PATRONYMIC = {
    ComponentMatch.EXACT: 0.20,
    ComponentMatch.TYPO: 0.12,
    ComponentMatch.INITIAL_COMPATIBLE: 0.05,
    # Unknown on at least one side: neither support nor contradiction.
    ComponentMatch.MISSING: 0.05,
}
EXACT_FORM_BONUS = 0.05
# The same complete form (name or known alias, no initials) is strong evidence
# whatever role reading wins: suffix hints may read "Дмитрий Шостакович" as a
# given name + patronymic, and a 4+ token name has no reading at all.
EXACT_COMPLETE_FORM = 0.85
ORDER_PENALTY = 0.05
CONFLICT_CAP = 0.25


class PersonResolutionScorer:
    def score(self, features: PersonResolutionFeatures) -> PersonResolutionScore:
        rules: list[str] = []
        value = 0.0
        for name, table, match in (
            ("surname", _SURNAME, features.surname),
            ("given_name", _GIVEN_NAME, features.given_name),
            ("patronymic", _PATRONYMIC, features.patronymic),
        ):
            if match in table:
                value += table[match]
                rules.append(f"{name}_{match.value}")
        if features.exact_name or features.exact_alias:
            value += EXACT_FORM_BONUS
            rules.append("exact_form")
        if (
            (features.exact_name or features.exact_alias)
            and not features.incomplete_name
            and not features.initials_only
            and value < EXACT_COMPLETE_FORM
        ):
            value = EXACT_COMPLETE_FORM
            rules.append("exact_complete_form")
        if features.order_differs:
            value -= ORDER_PENALTY
            rules.append("order_differs")
        if features.conflicts:
            value = min(value, CONFLICT_CAP)
            rules.append("conflict_cap")
        return PersonResolutionScore(
            resolution_score=round(min(max(value, 0.0), 1.0), 4), rules=rules
        )
