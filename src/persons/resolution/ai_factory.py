"""Wiring of the AI entity reviewer: the only place that picks a provider.

`ENTITY_REVIEW_PROVIDER=none` (the default) builds nothing: pending decisions keep going
straight to a human, exactly as before this feature.
"""

from __future__ import annotations

import httpx
from sqlalchemy.orm import Session, sessionmaker

from llm.cli_reviewer import CliEntityMatchReviewer
from llm.entity_reviewer import LlmEntityMatchReviewer
from llm.together_client import TogetherConfig, TogetherStructuredLlmClient
from persons.resolution.ai_policy import (
    EntityReviewProvider,
    EntityReviewSettings,
)
from persons.resolution.ai_review import EntityMatchReviewer
from persons.resolution.ai_review_service import AutomatedEntityReviewService


def build_entity_review_service(
    session_factory: sessionmaker[Session],
    settings: EntityReviewSettings,
) -> AutomatedEntityReviewService | None:
    """The review service, or None when no provider is configured."""
    if settings.provider is EntityReviewProvider.CLI:
        model = settings.model or settings.cli_command
        reviewer: EntityMatchReviewer = CliEntityMatchReviewer(
            settings.cli_command,
            model=model,
            provider=settings.provider.value,
            timeout_seconds=settings.timeout_seconds,
            prompt_version=settings.prompt_version,
        )
        return AutomatedEntityReviewService(
            session_factory, reviewer, settings=settings, model=model
        )
    if settings.provider is not EntityReviewProvider.TOGETHER:
        return None
    config = TogetherConfig.from_env()
    # The review has its own timeout: it is a small structured call, not a report.
    if settings.model is not None or settings.timeout_seconds != config.timeout_seconds:
        config = TogetherConfig(
            api_key=config.api_key,
            model=settings.model or config.model,
            timeout_seconds=settings.timeout_seconds,
            base_url=config.base_url,
        )
    reviewer = LlmEntityMatchReviewer(
        TogetherStructuredLlmClient(config, http_client=httpx.Client()),
        model=config.model,
        provider=settings.provider.value,
        prompt_version=settings.prompt_version,
    )
    return AutomatedEntityReviewService(
        session_factory, reviewer, settings=settings, model=config.model
    )
