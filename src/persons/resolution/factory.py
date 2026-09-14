"""Composition root for ER v2 (CLI, extraction pipeline, evaluation)."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping

from sqlalchemy.orm import Session, sessionmaker

from persons.persistence import SqlAlchemyPersonPersistence
from persons.resolution.candidates import (
    CandidateConfig,
    CompositeCandidateGenerator,
    ExactKeyCandidateGenerator,
    PersonCandidateGenerator,
    SemanticCandidateGenerator,
    TrigramCandidateGenerator,
)
from persons.resolution.decision import PersonResolutionDecisionPolicy, ResolutionThresholds
from persons.resolution.service import PersonResolutionEngine, PersonResolutionService
from persons.resolver import RuleBasedPersonResolver
from semantic_retrieval.retrievers import EntityRetriever

logger = logging.getLogger("person_resolution")


def build_generators(
    config: CandidateConfig, semantic_retriever: EntityRetriever | None = None
) -> list[PersonCandidateGenerator]:
    generators: list[PersonCandidateGenerator] = [
        ExactKeyCandidateGenerator(),
        TrigramCandidateGenerator(),
    ]
    if config.semantic_enabled and semantic_retriever is not None:
        generators.append(
            SemanticCandidateGenerator(semantic_retriever, min_score=config.semantic_min_score)
        )
    return generators


def semantic_retriever_from_env(
    session_factory: sessionmaker[Session], env: Mapping[str, str]
) -> EntityRetriever | None:
    """Dense Person retriever when ER semantic candidates are enabled and Qdrant is set."""
    from semantic_retrieval.factory import SemanticRetrievalConfig, create_semantic_components
    from semantic_retrieval.models import RetrievalBackend

    semantic = SemanticRetrievalConfig.from_env(env)
    if semantic.qdrant_url is None:
        return None
    components = create_semantic_components(session_factory, semantic, env, with_reranker=False)
    return components.retriever(RetrievalBackend.DENSE)


def build_person_resolution_engine(
    session_factory: sessionmaker[Session],
    env: Mapping[str, str] | None = None,
    *,
    semantic_retriever: EntityRetriever | None = None,
) -> PersonResolutionEngine:
    env = os.environ if env is None else env
    config = CandidateConfig.from_env(env)
    if config.semantic_enabled and semantic_retriever is None:
        semantic_retriever = semantic_retriever_from_env(session_factory, env)
        if semantic_retriever is None:
            logger.warning("er_semantic_candidates_disabled reason=QDRANT_URL is not set")
    return PersonResolutionEngine(
        persistence=SqlAlchemyPersonPersistence(session_factory),
        generator=CompositeCandidateGenerator(build_generators(config, semantic_retriever)),
        policy=PersonResolutionDecisionPolicy(ResolutionThresholds.from_env(env)),
        config=config,
    )


def build_person_resolution_service(
    session_factory: sessionmaker[Session],
    env: Mapping[str, str] | None = None,
    *,
    semantic_retriever: EntityRetriever | None = None,
    persistence: SqlAlchemyPersonPersistence | None = None,
    resolver: RuleBasedPersonResolver | None = None,
) -> PersonResolutionService:
    persistence = persistence or SqlAlchemyPersonPersistence(session_factory)
    return PersonResolutionService(
        engine=build_person_resolution_engine(
            session_factory, env, semantic_retriever=semantic_retriever
        ),
        persistence=persistence,
        resolver=resolver or RuleBasedPersonResolver(persistence),
    )
