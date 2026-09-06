from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

StrippedNonEmptyString = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
    ),
]


class ChunkReference(BaseModel):
    source_base_url: StrippedNonEmptyString
    external_id: StrippedNonEmptyString
    ordinal: int = Field(ge=0)


class EvaluationCase(BaseModel):
    query_id: StrippedNonEmptyString
    query_text: StrippedNonEmptyString
    expected_chunk: ChunkReference


class EvaluationDocument(BaseModel):
    source_base_url: StrippedNonEmptyString
    external_id: StrippedNonEmptyString
    canonical_url: StrippedNonEmptyString
    title: StrippedNonEmptyString
    chunks: list[StrippedNonEmptyString] = Field(min_length=1)


class EvaluationCaseResult(BaseModel):
    query_id: StrippedNonEmptyString
    query_text: StrippedNonEmptyString
    expected_chunk: ChunkReference
    retrieved_chunks: list[ChunkReference]
    reciprocal_rank: float = Field(ge=0.0, le=1.0)


class EvaluationReport(BaseModel):
    results: list[EvaluationCaseResult]
    mean_reciprocal_rank: float = Field(ge=0.0, le=1.0)
