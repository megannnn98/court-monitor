"""Typed inputs of Entity Resolution v2."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class NameRole(StrEnum):
    SURNAME = "surname"
    GIVEN_NAME = "given_name"
    PATRONYMIC = "patronymic"


class RoleSignal(StrEnum):
    """Heuristic hints about a role assignment. Hints order variants; they never
    remove one (foreign or indeclinable surnames break every suffix rule)."""

    PATRONYMIC_SUFFIX = "patronymic_suffix"
    SURNAME_SUFFIX = "surname_suffix"
    PATRONYMIC_SUFFIX_OUTSIDE_PATRONYMIC = "patronymic_suffix_outside_patronymic"
    SURNAME_SUFFIX_OUTSIDE_SURNAME = "surname_suffix_outside_surname"


class NameToken(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    is_initial: bool


class NameComponent(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: NameRole
    text: str
    is_initial: bool


class NameVariant(BaseModel):
    """One admissible reading of the tokens as surname / given name / patronymic."""

    model_config = ConfigDict(frozen=True)

    components: tuple[NameComponent, ...]
    signals: tuple[RoleSignal, ...] = ()

    def component(self, role: NameRole) -> NameComponent | None:
        return next((part for part in self.components if part.role is role), None)

    @property
    def plausibility(self) -> int:
        """Supporting minus contradicting hints; ordering only, not a decision input."""
        support = {RoleSignal.PATRONYMIC_SUFFIX, RoleSignal.SURNAME_SUFFIX}
        return sum(1 if signal in support else -1 for signal in self.signals)


class NormalizedPersonName(BaseModel):
    model_config = ConfigDict(frozen=True)

    raw: str
    tokens: tuple[NameToken, ...]
    # Lowercased, `ё`→`е`, punctuation-free tokens in the original order.
    canonical_form: str
    # All admissible role readings, most plausible first; empty when unparseable.
    variants: tuple[NameVariant, ...]
    # Sorted full tokens: order-independent, so reordered forms of one name share
    # them, and any two readings of one person share at least the surname.
    block_keys: tuple[str, ...]

    @property
    def full_tokens(self) -> tuple[str, ...]:
        return tuple(token.text for token in self.tokens if not token.is_initial)

    @property
    def has_initials(self) -> bool:
        return any(token.is_initial for token in self.tokens)

    @property
    def is_incomplete(self) -> bool:
        """A single full token: surname or given name alone."""
        return len(self.full_tokens) <= 1 and not self.has_initials


class PersonIdentityInput(BaseModel):
    """What ER v2 knows about an incoming person mention.

    Only data the extraction pipeline really provides: there is no birth date
    extraction, so no birth date field.
    """

    name: str
    surface_text: str | None = None
    matching_key: str | None = None
    aliases: list[str] = Field(default_factory=list)
    mention_id: int | None = None
    source_id: int | None = None
    article_id: int | None = None
    event_types: list[str] = Field(default_factory=list)


class CandidateSource(StrEnum):
    ALIAS = "alias"
    TRIGRAM = "trigram"
    SEMANTIC = "semantic"


class PersonResolutionCandidate(BaseModel):
    """A person worth comparing: generation is recall-oriented, not a decision."""

    person_id: int
    canonical_name: str
    matching_key: str
    aliases: list[str] = Field(default_factory=list)
    sources: list[CandidateSource] = Field(default_factory=list)
    trigram_similarity: float | None = None
    # Dense cosine of the Person document; diagnostics only, never identity evidence.
    semantic_similarity: float | None = None


class ComponentMatch(StrEnum):
    EXACT = "exact"
    TYPO = "typo"
    INITIAL_COMPATIBLE = "initial_compatible"
    MISSING = "missing"
    MISMATCH = "mismatch"


class IdentityConflict(StrEnum):
    SURNAME_MISMATCH = "surname_mismatch"
    GIVEN_NAME_MISMATCH = "given_name_mismatch"
    PATRONYMIC_MISMATCH = "patronymic_mismatch"


class PersonResolutionFeatures(BaseModel):
    """Deterministic comparison of an incoming name with one candidate.

    Components come from the best-aligned pair of name variants (incoming
    reading x candidate name or alias reading).
    """

    compared_form: str
    compared_form_is_alias: bool
    exact_matching_key: bool
    exact_name: bool
    exact_alias: bool
    surname: ComponentMatch
    given_name: ComponentMatch
    patronymic: ComponentMatch
    surname_similarity: float | None
    given_name_similarity: float | None
    patronymic_similarity: float | None
    full_name_similarity: float
    order_differs: bool
    initials_only: bool
    incomplete_name: bool
    conflicts: list[IdentityConflict] = Field(default_factory=list)
    semantic_similarity: float | None = None


class PersonResolutionScore(BaseModel):
    """Rule-based ordinal score. Not a calibrated probability of identity."""

    resolution_score: float = Field(ge=0.0, le=1.0)
    rules: list[str] = Field(default_factory=list)


class ScoredPersonCandidate(BaseModel):
    candidate: PersonResolutionCandidate
    features: PersonResolutionFeatures
    score: PersonResolutionScore

    @property
    def person_id(self) -> int:
        return self.candidate.person_id

    @property
    def resolution_score(self) -> float:
        return self.score.resolution_score

    @property
    def is_conflicting(self) -> bool:
        return bool(self.features.conflicts)


class PersonResolutionAction(StrEnum):
    AUTO_LINK = "auto_link"
    CREATE_NEW = "create_new"
    REVIEW = "review"


class PersonResolutionReason(StrEnum):
    EXACT_MATCHING_KEY = "exact_matching_key"
    STRONG_UNIQUE_MATCH = "strong_unique_match"
    NO_CANDIDATE = "no_candidate"
    NO_PLAUSIBLE_CANDIDATE = "no_plausible_candidate"
    MULTIPLE_PLAUSIBLE_CANDIDATES = "multiple_plausible_candidates"
    POSSIBLE_DUPLICATE_PERSONS = "possible_duplicate_persons"
    INCOMPLETE_NAME = "incomplete_name"
    INITIALS_ONLY = "initials_only"
    CONFLICTING_IDENTITY_DATA = "conflicting_identity_data"
    LOW_DECISION_MARGIN = "low_decision_margin"
    MEDIUM_CONFIDENCE_MATCH = "medium_confidence_match"
    SEMANTIC_SOURCE_UNAVAILABLE = "semantic_source_unavailable"


class SemanticSourceStatus(StrEnum):
    DISABLED = "disabled"
    OK = "ok"
    UNAVAILABLE = "unavailable"


class PersonResolutionDecision(BaseModel):
    action: PersonResolutionAction
    selected_person_id: int | None = None
    candidates: list[ScoredPersonCandidate] = Field(default_factory=list)
    reasons: list[PersonResolutionReason] = Field(default_factory=list)
    # top1 - top2 over plausible candidates; None with fewer than one.
    decision_margin: float | None = None
    semantic_source: SemanticSourceStatus = SemanticSourceStatus.DISABLED
