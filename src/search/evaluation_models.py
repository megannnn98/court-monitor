from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

StrippedNonEmptyString = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
    ),
]


class ArticleReference(BaseModel):
    source_base_url: StrippedNonEmptyString
    external_id: StrippedNonEmptyString


class EvaluationCase(BaseModel):
    query_id: StrippedNonEmptyString
    query_text: StrippedNonEmptyString
    expected_article: ArticleReference


class EvaluationDocument(BaseModel):
    source_base_url: StrippedNonEmptyString
    external_id: StrippedNonEmptyString
    canonical_url: StrippedNonEmptyString
    title: StrippedNonEmptyString
    text: StrippedNonEmptyString


class EvaluationCaseResult(BaseModel):
    query_id: StrippedNonEmptyString
    query_text: StrippedNonEmptyString
    expected_article: ArticleReference
    retrieved_articles: list[ArticleReference]
    reciprocal_rank: float = Field(ge=0.0, le=1.0)


class EvaluationReport(BaseModel):
    results: list[EvaluationCaseResult]
    mean_reciprocal_rank: float = Field(ge=0.0, le=1.0)
