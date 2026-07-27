"""Pydantic v2 DTOs for extracted data.

These DTOs validate the boundary between unreliable extractors (regex, LLM)
and the rest of the system. Storage ORM lives in court_monitor.storage.orm.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from court_monitor.domain.models import VerificationStatus


class ExtractedFactDTO(BaseModel):
    """A single extracted value with its provenance and confidence — spec §3.1, §7.

    Every value flowing into the DB must be expressed as one of these. An
    inferred value MUST NOT silently become confirmed downstream.
    """

    model_config = ConfigDict(extra="forbid")

    entity: str = Field(description="Logical entity: 'document', 'person', 'case', ...")
    field: str = Field(description="Field name on the entity, e.g. 'criminal_article'")
    value: Any
    verification_status: VerificationStatus = VerificationStatus.inferred
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    quote: str | None = None
    source_url: str | None = None
    extraction_method: str = Field(description="e.g. 'regex:article', 'llm:openai', 'manual'")
