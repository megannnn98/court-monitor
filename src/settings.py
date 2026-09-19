"""Startup validation of the whole application configuration (ADR 0014).

Every subsystem keeps its own typed settings (`DatabasePoolSettings`,
`MonitoringSettings`, `SemanticRetrievalConfig`, ER `CandidateConfig` /
`ResolutionThresholds`, `TogetherConfig`). This module only loads all of them
at once, so a bad value fails the process start — API lifespan, CLI, Dagster
code location — with every problem listed, instead of a stack trace on the
first request that happens to need it.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, TypeVar

from sqlalchemy.engine import make_url

from db.database import DatabasePoolSettings, validate_database_url
from monitoring.models import MonitoringSettings
from persons.resolution.candidates import CandidateConfig
from persons.resolution.decision import ResolutionThresholds
from research.workflow.llm import LlmConfigurationError
from semantic_retrieval.embeddings import EmbeddingConfig
from semantic_retrieval.factory import SemanticRetrievalConfig
from semantic_retrieval.models import SemanticConfigurationError
from semantic_retrieval.relevance import resolve_dense_min_score

T = TypeVar("T")

# Settings whose values must never be printed or returned.
SECRET_SETTINGS = frozenset({"TOGETHER_API_KEY", "POSTGRES_PASSWORD", "DAGSTER_PG_PASSWORD"})


class ApplicationConfigurationError(ValueError):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("Invalid configuration:\n- " + "\n- ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class TogetherSettings:
    """Together AI is optional: only natural-language research needs it."""

    configured: bool
    model: str | None
    timeout_seconds: float | None


@dataclass(frozen=True)
class ApplicationSettings:
    database_url: str
    database_pool: DatabasePoolSettings
    monitoring: MonitoringSettings
    semantic: SemanticRetrievalConfig
    # Only when semantic retrieval is configured.
    embedding: EmbeddingConfig | None
    dense_min_score: float | None
    er_candidates: CandidateConfig
    er_thresholds: ResolutionThresholds
    together: TogetherSettings

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None, *, require_database: bool = True
    ) -> ApplicationSettings:
        env = os.environ if env is None else env
        problems: list[str] = []

        def load(loader: Callable[[], T]) -> T | None:
            try:
                return loader()
            except (ValueError, LlmConfigurationError, SemanticConfigurationError) as exc:
                problems.append(str(exc))
                return None

        database_url = (env.get("DATABASE_URL") or "").strip()
        if not database_url:
            if require_database:
                problems.append("DATABASE_URL environment variable is not set")
        else:
            load(lambda: validate_database_url(database_url))
        pool = load(lambda: DatabasePoolSettings.from_env(env))
        monitoring = load(lambda: MonitoringSettings.from_env(env))
        semantic = load(lambda: SemanticRetrievalConfig.from_env(env))
        er_candidates = load(lambda: CandidateConfig.from_env(env))
        er_thresholds = load(lambda: ResolutionThresholds.from_env(env))

        embedding: EmbeddingConfig | None = None
        dense_min_score: float | None = None
        if semantic is not None and semantic.enabled:
            embedding = load(lambda: EmbeddingConfig.from_env(env))
            if embedding is not None:
                model_id = embedding.model_id
                dense_min_score = load(lambda: resolve_dense_min_score(env, model_id))

        together = load(lambda: _together_settings(env))

        if problems:
            raise ApplicationConfigurationError(problems)
        assert pool is not None and monitoring is not None and semantic is not None
        assert er_candidates is not None and er_thresholds is not None and together is not None
        return cls(
            database_url=database_url,
            database_pool=pool,
            monitoring=monitoring,
            semantic=semantic,
            embedding=embedding,
            dense_min_score=dense_min_score,
            er_candidates=er_candidates,
            er_thresholds=er_thresholds,
            together=together,
        )

    def redacted(self) -> dict[str, Any]:
        """Configuration safe to print: no passwords, no API keys."""
        return {
            "database": {
                "url": _redact_url(self.database_url) if self.database_url else None,
                "pool_size": self.database_pool.pool_size,
                "max_overflow": self.database_pool.max_overflow,
                "pool_timeout": self.database_pool.pool_timeout,
                "connect_timeout": self.database_pool.connect_timeout,
            },
            "monitoring": {
                "enabled_sources": list(self.monitoring.enabled_sources),
                "cron": self.monitoring.cron,
                "discovery_limit": self.monitoring.discovery_limit,
                "stale_run_after_minutes": int(
                    self.monitoring.stale_run_after.total_seconds() // 60
                ),
            },
            "semantic": {
                "configured": self.semantic.enabled,
                "vector_backend": self.semantic.vector_backend,
                "qdrant_url": self.semantic.qdrant_url,
                "embedding_model_id": None if self.embedding is None else self.embedding.model_id,
                "dense_min_score": self.dense_min_score,
                "rerank": self.semantic.rerank,
            },
            "entity_resolution": {
                "candidate_limit": self.er_candidates.candidate_limit,
                "auto_link_min_score": self.er_thresholds.auto_link_min_score,
                "review_min_score": self.er_thresholds.review_min_score,
                "min_margin": self.er_thresholds.min_margin,
            },
            "together": {
                "configured": self.together.configured,
                "model": self.together.model,
                "timeout_seconds": self.together.timeout_seconds,
            },
        }


def _together_settings(env: Mapping[str, str]) -> TogetherSettings:
    from llm.together_client import TogetherConfig

    if (
        not (env.get("TOGETHER_API_KEY") or "").strip()
        and not (env.get("TOGETHER_MODEL") or "").strip()
    ):
        return TogetherSettings(configured=False, model=None, timeout_seconds=None)
    # Partially configured or an invalid timeout is an error, not "not configured".
    config = TogetherConfig.from_env(env)
    return TogetherSettings(
        configured=True, model=config.model, timeout_seconds=config.timeout_seconds
    )


def _redact_url(database_url: str) -> str:
    return make_url(database_url).render_as_string(hide_password=True)
