"""Composition root for ER v2 (CLI, extraction pipeline, evaluation)."""

from __future__ import annotations

import os
from collections.abc import Mapping

from sqlalchemy.orm import Session, sessionmaker

from persons.persistence import SqlAlchemyPersonPersistence
from persons.resolution.candidates import (
    CandidateConfig,
    CompositeCandidateGenerator,
    ExactKeyCandidateGenerator,
    PersonCandidateGenerator,
    TrigramCandidateGenerator,
)
from persons.resolution.decision import PersonResolutionDecisionPolicy, ResolutionThresholds
from persons.resolution.service import PersonResolutionEngine, PersonResolutionService


def build_generators() -> list[PersonCandidateGenerator]:
    return [ExactKeyCandidateGenerator(), TrigramCandidateGenerator()]


def build_person_resolution_engine(
    session_factory: sessionmaker[Session],
    env: Mapping[str, str] | None = None,
) -> PersonResolutionEngine:
    env = os.environ if env is None else env
    config = CandidateConfig.from_env(env)
    return PersonResolutionEngine(
        persistence=SqlAlchemyPersonPersistence(session_factory),
        generator=CompositeCandidateGenerator(build_generators()),
        policy=PersonResolutionDecisionPolicy(ResolutionThresholds.from_env(env)),
        config=config,
    )


def build_person_resolution_service(
    session_factory: sessionmaker[Session],
    env: Mapping[str, str] | None = None,
    *,
    persistence: SqlAlchemyPersonPersistence | None = None,
) -> PersonResolutionService:
    persistence = persistence or SqlAlchemyPersonPersistence(session_factory)
    return PersonResolutionService(
        engine=build_person_resolution_engine(session_factory, env),
        persistence=persistence,
    )
