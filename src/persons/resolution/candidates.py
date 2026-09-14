"""Candidate generation for ER v2: recall-oriented, bounded, never a decision.

Every generator narrows inside PostgreSQL or Qdrant and returns at most `limit`
persons; the Person table is never scanned in Python.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy import func, literal, or_, select, union_all
from sqlalchemy.orm import InstrumentedAttribute, Session
from sqlalchemy.sql.elements import ColumnElement

from db.orm_models import PersonAliasRecord, PersonRecord
from persons.models import PersonStatus
from persons.resolution.models import (
    CandidateSource,
    PersonIdentityInput,
    PersonResolutionCandidate,
    SemanticSourceStatus,
)
from persons.resolution.normalizer import PersonNameNormalizer
from semantic_retrieval.models import RetrievalEntityType, RetrievalError, RetrievalQuery
from semantic_retrieval.relevance import dense_similarity
from semantic_retrieval.retrievers import EntityRetriever

logger = logging.getLogger("person_resolution")

DEFAULT_CANDIDATE_LIMIT = 30
MAX_CANDIDATE_LIMIT = 200
# Aliases loaded per candidate for feature extraction.
MAX_ALIASES_PER_CANDIDATE = 20
# pg_trgm: whole-name similarity and per-token word similarity lower bounds.
TRIGRAM_NAME_MIN_SIMILARITY = 0.3
TRIGRAM_TOKEN_MIN_WORD_SIMILARITY = 0.5
MIN_BLOCK_TOKEN_LENGTH = 3


class CandidateSourceUnavailableError(RuntimeError):
    """An optional candidate source could not answer (e.g. Qdrant is down)."""


class PersonCandidateGenerator(Protocol):
    source: CandidateSource

    def generate(
        self, identity: PersonIdentityInput, *, limit: int, session: Session
    ) -> list[PersonResolutionCandidate]: ...


@dataclass(frozen=True)
class CandidateConfig:
    candidate_limit: int = DEFAULT_CANDIDATE_LIMIT
    semantic_enabled: bool = False
    # Candidate generation only; never an identity threshold (ADR 0012).
    semantic_min_score: float | None = None

    def __post_init__(self) -> None:
        if not 1 <= self.candidate_limit <= MAX_CANDIDATE_LIMIT:
            raise ValueError(f"ER_CANDIDATE_LIMIT must be between 1 and {MAX_CANDIDATE_LIMIT}")
        if self.semantic_min_score is not None and not -1.0 <= self.semantic_min_score <= 1.0:
            raise ValueError("ER_SEMANTIC_CANDIDATE_MIN_SCORE must be within [-1, 1]")

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> CandidateConfig:
        try:
            limit = int(env.get("ER_CANDIDATE_LIMIT") or DEFAULT_CANDIDATE_LIMIT)
            raw_min = env.get("ER_SEMANTIC_CANDIDATE_MIN_SCORE")
            min_score = float(raw_min) if raw_min else None
        except ValueError as exc:
            raise ValueError(f"invalid ER candidate configuration: {exc}") from exc
        return cls(
            candidate_limit=limit,
            semantic_enabled=(env.get("ER_SEMANTIC_CANDIDATES") or "").strip().lower()
            in {"1", "true", "yes", "on"},
            semantic_min_score=min_score,
        )


def load_candidates(
    session: Session, person_ids: Sequence[int], source: CandidateSource
) -> dict[int, PersonResolutionCandidate]:
    """Active persons with their aliases, keyed by id."""
    if not person_ids:
        return {}
    persons = session.execute(
        select(PersonRecord.id, PersonRecord.normalized_name, PersonRecord.matching_key).where(
            PersonRecord.id.in_(person_ids), PersonRecord.status == PersonStatus.ACTIVE.value
        )
    ).all()
    aliases: dict[int, list[str]] = defaultdict(list)
    for person_id, alias in session.execute(
        select(PersonAliasRecord.person_id, PersonAliasRecord.normalized_text)
        .where(PersonAliasRecord.person_id.in_([row.id for row in persons]))
        .order_by(PersonAliasRecord.person_id, PersonAliasRecord.id)
    ).all():
        if len(aliases[person_id]) < MAX_ALIASES_PER_CANDIDATE and alias not in aliases[person_id]:
            aliases[person_id].append(alias)
    return {
        row.id: PersonResolutionCandidate(
            person_id=row.id,
            canonical_name=row.normalized_name,
            matching_key=row.matching_key,
            aliases=aliases[row.id],
            sources=[source],
        )
        for row in persons
    }


class ExactKeyCandidateGenerator:
    """Persons whose name (`exact_key`) or known alias (`alias`) has the incoming key.

    Zero, one or several namesakes: `matching_key` is a candidate lookup key, not
    an identity key (ADR 0012). Ordered by person id; the decision never depends on it.
    """

    source = CandidateSource.EXACT_KEY

    def generate(
        self, identity: PersonIdentityInput, *, limit: int, session: Session
    ) -> list[PersonResolutionCandidate]:
        if not identity.matching_key:
            return []
        key = identity.matching_key
        rows = session.execute(
            select(PersonRecord.id, PersonRecord.matching_key)
            .where(
                PersonRecord.status == PersonStatus.ACTIVE.value,
                or_(
                    PersonRecord.matching_key == key,
                    PersonRecord.id.in_(
                        select(PersonAliasRecord.person_id).where(
                            PersonAliasRecord.matching_key == key
                        )
                    ),
                ),
            )
            .order_by(PersonRecord.id)
            .limit(limit)
        ).all()
        if not rows:
            return []
        alias_keyed = set(
            session.scalars(
                select(PersonAliasRecord.person_id).where(
                    PersonAliasRecord.matching_key == key,
                    PersonAliasRecord.person_id.in_([row.id for row in rows]),
                )
            ).all()
        )
        loaded = load_candidates(session, [row.id for row in rows], self.source)
        candidates = []
        for row in rows:
            if row.id not in loaded:
                continue
            sources = []
            if row.matching_key == key:
                sources.append(CandidateSource.EXACT_KEY)
            if row.id in alias_keyed:
                sources.append(CandidateSource.ALIAS)
            candidates.append(loaded[row.id].model_copy(update={"sources": sorted(sources)}))
        logger.info(
            "er_exact_candidates_found mention_id=%s key_matches=%d alias_matches=%d",
            identity.mention_id,
            sum(CandidateSource.EXACT_KEY in c.sources for c in candidates),
            sum(CandidateSource.ALIAS in c.sources for c in candidates),
        )
        return candidates


class TrigramCandidateGenerator:
    """Fuzzy lexical candidates from pg_trgm GIN indexes on names and aliases.

    A person qualifies when the whole name is trigram-similar, or when one full
    incoming token is word-similar to a name (the surname block that catches
    reordered names, initials and single-token typos).
    """

    source = CandidateSource.TRIGRAM

    def __init__(self, normalizer: PersonNameNormalizer | None = None) -> None:
        self._normalizer = normalizer or PersonNameNormalizer()

    def generate(
        self, identity: PersonIdentityInput, *, limit: int, session: Session
    ) -> list[PersonResolutionCandidate]:
        name = self._normalizer.normalize(identity.name)
        if not name.tokens:
            return []
        query = name.canonical_form
        tokens = [token for token in name.full_tokens if len(token) >= MIN_BLOCK_TOKEN_LENGTH]
        # `%` and `<%` use the index; thresholds are transaction-local settings.
        session.execute(
            select(
                func.set_config(
                    "pg_trgm.similarity_threshold", str(TRIGRAM_NAME_MIN_SIMILARITY), True
                ),
                func.set_config(
                    "pg_trgm.word_similarity_threshold",
                    str(TRIGRAM_TOKEN_MIN_WORD_SIMILARITY),
                    True,
                ),
            )
        )

        def matches(
            column: InstrumentedAttribute[str],
        ) -> tuple[ColumnElement[bool], ColumnElement[float]]:
            conditions: list[ColumnElement[bool]] = [column.op("%")(query)]
            conditions += [literal(token).op("<%")(column) for token in tokens]
            scores: list[ColumnElement[float]] = [func.similarity(column, query)]
            scores += [func.word_similarity(literal(token), column) for token in tokens]
            best = func.greatest(*scores) if len(scores) > 1 else scores[0]
            return or_(*conditions), best

        person_match, person_score = matches(PersonRecord.normalized_name)
        alias_match, alias_score = matches(PersonAliasRecord.normalized_text)
        hits = union_all(
            select(PersonRecord.id.label("person_id"), person_score.label("score")).where(
                PersonRecord.status == PersonStatus.ACTIVE.value, person_match
            ),
            select(PersonAliasRecord.person_id.label("person_id"), alias_score.label("score"))
            .join(PersonRecord, PersonRecord.id == PersonAliasRecord.person_id)
            .where(PersonRecord.status == PersonStatus.ACTIVE.value, alias_match),
        ).subquery()
        best = func.max(hits.c.score)
        rows = session.execute(
            select(hits.c.person_id, best.label("score"))
            .group_by(hits.c.person_id)
            .order_by(best.desc(), hits.c.person_id)
            .limit(limit)
        ).all()
        loaded = load_candidates(session, [row.person_id for row in rows], self.source)
        candidates = []
        for row in rows:
            if (candidate := loaded.get(row.person_id)) is not None:
                candidates.append(
                    candidate.model_copy(update={"trigram_similarity": round(float(row.score), 4)})
                )
        return candidates


class SemanticCandidateGenerator:
    """Dense Person-document neighbours of the incoming name (optional, ADR 0012).

    Uses its own ER threshold, not the research relevance threshold. A hit only
    adds a candidate to compare; its similarity is never identity evidence.
    """

    source = CandidateSource.SEMANTIC

    def __init__(self, retriever: EntityRetriever, *, min_score: float | None = None) -> None:
        self._retriever = retriever
        self._min_score = min_score

    def generate(
        self, identity: PersonIdentityInput, *, limit: int, session: Session
    ) -> list[PersonResolutionCandidate]:
        try:
            result = self._retriever.retrieve(
                RetrievalQuery(
                    text=identity.name, entity_type=RetrievalEntityType.PERSON, limit=limit
                )
            )
        except RetrievalError as exc:
            raise CandidateSourceUnavailableError(str(exc)) from exc
        scores = {
            hit.entity_id: similarity
            for hit in result.hits
            if (similarity := dense_similarity(hit)) is not None
            and (self._min_score is None or similarity >= self._min_score)
        }
        loaded = load_candidates(session, list(scores), self.source)
        return [
            loaded[person_id].model_copy(update={"semantic_similarity": round(score, 4)})
            for person_id, score in scores.items()
            if person_id in loaded
        ]


@dataclass(frozen=True)
class CandidateGenerationResult:
    candidates: list[PersonResolutionCandidate]
    semantic_source: SemanticSourceStatus
    counts: dict[CandidateSource, int] = field(default_factory=dict)


def _order_key(candidate: PersonResolutionCandidate) -> tuple[int, float, float, int]:
    return (
        0 if {CandidateSource.EXACT_KEY, CandidateSource.ALIAS} & set(candidate.sources) else 1,
        -(candidate.trigram_similarity or 0.0),
        -(candidate.semantic_similarity or 0.0),
        candidate.person_id,
    )


class CompositeCandidateGenerator:
    """Runs generators, merges candidates by person, keeps a deterministic top `limit`."""

    def __init__(self, generators: Sequence[PersonCandidateGenerator]) -> None:
        self._generators = list(generators)

    def generate(
        self, identity: PersonIdentityInput, *, limit: int, session: Session
    ) -> CandidateGenerationResult:
        merged: dict[int, PersonResolutionCandidate] = {}
        counts: dict[CandidateSource, int] = {}
        semantic = SemanticSourceStatus.DISABLED
        for generator in self._generators:
            try:
                found = generator.generate(identity, limit=limit, session=session)
            except CandidateSourceUnavailableError as exc:
                logger.warning(
                    "er_candidate_source_unavailable source=%s error=%s",
                    generator.source.value,
                    exc,
                )
                if generator.source is CandidateSource.SEMANTIC:
                    semantic = SemanticSourceStatus.UNAVAILABLE
                continue
            if generator.source is CandidateSource.SEMANTIC:
                semantic = SemanticSourceStatus.OK
            counts[generator.source] = len(found)
            for candidate in found:
                merged[candidate.person_id] = _merge(merged.get(candidate.person_id), candidate)
        candidates = sorted(merged.values(), key=_order_key)[:limit]
        return CandidateGenerationResult(candidates, semantic, counts)


def _merge(
    existing: PersonResolutionCandidate | None, new: PersonResolutionCandidate
) -> PersonResolutionCandidate:
    if existing is None:
        return new
    return existing.model_copy(
        update={
            "sources": sorted({*existing.sources, *new.sources}),
            "trigram_similarity": _max(existing.trigram_similarity, new.trigram_similarity),
            "semantic_similarity": _max(existing.semantic_similarity, new.semantic_similarity),
        }
    )


def _max(left: float | None, right: float | None) -> float | None:
    values = [value for value in (left, right) if value is not None]
    return max(values) if values else None
