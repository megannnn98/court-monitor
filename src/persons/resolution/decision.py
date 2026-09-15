"""Decision policy: AUTO_LINK / REVIEW / CREATE_NEW from scored candidates.

Rules (ADR 0012), in order:
1. no candidates → CREATE_NEW (NO_CANDIDATE);
2. plausible = no identity conflict and score ≥ review minimum;
3. top plausible ≥ auto-link minimum, margin over the second plausible above
   the minimum, a single strong candidate (no possible duplicate persons), a
   single active person with the incoming matching_key (namesakes are never
   picked by id), a full (non-initial, complete) incoming name and identity
   evidence beyond the name — the full name with patronymic, or the candidate
   already mentioned in the same article — → AUTO_LINK;
   otherwise REVIEW with every reason that blocked it;
4. top plausible below the auto-link minimum → REVIEW (MEDIUM_CONFIDENCE_MATCH);
5. nothing plausible → CREATE_NEW, unless the semantic source was unavailable
   and a non-conflicting candidate shares the surname: then linking vs creating
   stays open without it → REVIEW.
0. a surname alone (no given name, no initials) whose only same-surname candidate is
   already mentioned in the same article → AUTO_LINK (SAME_ARTICLE_SURNAME_REFERENCE);
   two such persons in the article (father and son) fall through to the rules below.
Semantic similarity never enters these rules except through (5).
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from persons.resolution.models import (
    ComponentMatch,
    PersonIdentityInput,
    PersonResolutionAction,
    PersonResolutionDecision,
    PersonResolutionReason,
    ScoredPersonCandidate,
    SemanticSourceStatus,
)

R = PersonResolutionReason


@dataclass(frozen=True)
class ResolutionThresholds:
    # Chosen on tests/fixtures/er_v2_corpus.json (ADR 0012): 0.85 is the lowest
    # auto-link minimum without a false link; 0.40 the highest review minimum
    # without a missed link. The margin is not discriminated by that corpus.
    auto_link_min_score: float = 0.85
    review_min_score: float = 0.4
    min_margin: float = 0.1

    def __post_init__(self) -> None:
        if not 0.0 <= self.review_min_score <= self.auto_link_min_score <= 1.0:
            raise ValueError("expected 0 <= ER_REVIEW_MIN_SCORE <= ER_AUTO_LINK_MIN_SCORE <= 1")
        if not 0.0 <= self.min_margin <= 1.0:
            raise ValueError("ER_MIN_MARGIN must be within [0, 1]")

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> ResolutionThresholds:
        defaults = cls()
        return cls(
            auto_link_min_score=_float(env, "ER_AUTO_LINK_MIN_SCORE", defaults.auto_link_min_score),
            review_min_score=_float(env, "ER_REVIEW_MIN_SCORE", defaults.review_min_score),
            min_margin=_float(env, "ER_MIN_MARGIN", defaults.min_margin),
        )


def _float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc


def rank_candidates(candidates: Sequence[ScoredPersonCandidate]) -> list[ScoredPersonCandidate]:
    return sorted(candidates, key=lambda c: (-c.resolution_score, c.person_id))


class PersonResolutionDecisionPolicy:
    def __init__(self, thresholds: ResolutionThresholds | None = None) -> None:
        self.thresholds = thresholds or ResolutionThresholds()

    def decide(
        self,
        identity: PersonIdentityInput,
        candidates: Sequence[ScoredPersonCandidate],
        *,
        semantic_source: SemanticSourceStatus = SemanticSourceStatus.DISABLED,
        distinct_pairs: Collection[frozenset[int]] = (),
    ) -> PersonResolutionDecision:
        """`distinct_pairs`: person pairs a reviewer declared different people (KEEP_SEPARATE)."""
        ranked = rank_candidates(candidates)

        def decision(
            action: PersonResolutionAction,
            reasons: list[PersonResolutionReason],
            *,
            selected: int | None = None,
            margin: float | None = None,
        ) -> PersonResolutionDecision:
            return PersonResolutionDecision(
                action=action,
                selected_person_id=selected,
                candidates=ranked,
                reasons=reasons,
                decision_margin=margin,
                semantic_source=semantic_source,
            )

        # A single name token (a surname repeated in the text) is a reference, not an
        # identity: it may link through the same-article rule below, but never creates a
        # canonical person of its own.
        single_token = len(identity.name.split()) < 2
        if not ranked:
            if single_token:
                return decision(PersonResolutionAction.REVIEW, [R.NO_CANDIDATE, R.INCOMPLETE_NAME])
            return decision(PersonResolutionAction.CREATE_NEW, [R.NO_CANDIDATE])

        # Within-article coreference: «Зареме Мусаевой… Мусаеву признали виновной».
        referenced = [
            c
            for c in ranked
            if c.features.same_article_mention
            and not c.is_conflicting
            and c.features.incomplete_name
            and not c.features.initials_only
            and c.features.surname in (ComponentMatch.EXACT, ComponentMatch.TYPO)
            and c.features.given_name is ComponentMatch.MISSING
        ]
        if len(referenced) == 1:
            return decision(
                PersonResolutionAction.AUTO_LINK,
                [R.SAME_ARTICLE_SURNAME_REFERENCE],
                selected=referenced[0].person_id,
            )
        if len(referenced) > 1:
            # The surname refers to one of several persons of this article: never a new
            # person, never a guess.
            return decision(
                PersonResolutionAction.REVIEW,
                [R.MULTIPLE_PLAUSIBLE_CANDIDATES, R.INCOMPLETE_NAME],
            )

        plausible = [
            c
            for c in ranked
            if not c.is_conflicting and c.resolution_score >= self.thresholds.review_min_score
        ]
        if not plausible:
            reasons = [R.NO_PLAUSIBLE_CANDIDATE]
            if any(c.is_conflicting for c in ranked):
                reasons.append(R.CONFLICTING_IDENTITY_DATA)
            if semantic_source is SemanticSourceStatus.UNAVAILABLE and any(
                not c.is_conflicting
                and c.features.surname in (ComponentMatch.EXACT, ComponentMatch.TYPO)
                for c in ranked
            ):
                return decision(
                    PersonResolutionAction.REVIEW, [*reasons, R.SEMANTIC_SOURCE_UNAVAILABLE]
                )
            if single_token:
                return decision(PersonResolutionAction.REVIEW, [*reasons, R.INCOMPLETE_NAME])
            return decision(PersonResolutionAction.CREATE_NEW, reasons)

        top = plausible[0]
        second = plausible[1].resolution_score if len(plausible) > 1 else 0.0
        margin = round(top.resolution_score - second, 4)
        reasons = []
        if len(plausible) > 1:
            reasons.append(R.MULTIPLE_PLAUSIBLE_CANDIDATES)
        exact_key = [c for c in plausible if c.features.exact_matching_key]
        if len(exact_key) > 1:
            # Same name is not same person: namesakes are never told apart by id or score.
            reasons.append(R.MULTIPLE_EXACT_NAME_MATCHES)
        strong = [c for c in plausible if c.resolution_score >= self.thresholds.auto_link_min_score]
        if len(strong) > 1:
            known_distinct = all(
                frozenset((left.person_id, right.person_id)) in distinct_pairs
                for index, left in enumerate(strong)
                for right in strong[index + 1 :]
            )
            reasons.append(
                R.KNOWN_DISTINCT_PERSONS if known_distinct else R.POSSIBLE_DUPLICATE_PERSONS
            )
        if top.features.initials_only:
            reasons.append(R.INITIALS_ONLY)
        if top.features.incomplete_name:
            reasons.append(R.INCOMPLETE_NAME)
        if not top.features.full_identity_match and not top.features.same_article_mention:
            reasons.append(R.NAME_ONLY_EVIDENCE)

        if top.resolution_score < self.thresholds.auto_link_min_score:
            return decision(
                PersonResolutionAction.REVIEW, [R.MEDIUM_CONFIDENCE_MATCH, *reasons], margin=margin
            )
        if margin <= self.thresholds.min_margin and len(plausible) > 1:
            reasons.insert(0, R.LOW_DECISION_MARGIN)
        blocked = {
            R.LOW_DECISION_MARGIN,
            R.INITIALS_ONLY,
            R.INCOMPLETE_NAME,
            # Two strong matches: possibly duplicate canonical persons, a human decides.
            R.POSSIBLE_DUPLICATE_PERSONS,
            R.KNOWN_DISTINCT_PERSONS,
            R.MULTIPLE_EXACT_NAME_MATCHES,
            R.NAME_ONLY_EVIDENCE,
        }
        if blocked & set(reasons):
            return decision(PersonResolutionAction.REVIEW, reasons, margin=margin)
        return decision(
            PersonResolutionAction.AUTO_LINK,
            [R.STRONG_UNIQUE_MATCH, *reasons],
            selected=top.person_id,
            margin=margin,
        )
