"""Sources, fetched documents and parsed articles."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base


class Source(Base):
    __tablename__ = "sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    base_url: Mapped[str] = mapped_column(String(2048), unique=True, nullable=False)
    # The Airtable record this row was last matched to, once a sync has named it. NULL
    # for a source the operator never listed; the row stays, since the code registry
    # and the articles are what make a source exist.
    external_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    # Cleared by a sync when the source is no longer worth loading. Nothing deletes the
    # row or its articles.
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SourceDocument(Base):
    __tablename__ = "source_documents"
    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "external_id",
            name="uq_source_documents_source_id_external_id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    canonical_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ParsedArticleRecord(Base):
    __tablename__ = "parsed_articles"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            name="uq_parsed_articles_document_id",
        ),
        Index(
            "ix_parsed_articles_search_vector_gin",
            "search_vector",
            postgresql_using="gin",
        ),
        # "People in the news of a period" filters articles by publication date.
        Index("ix_parsed_articles_published_at", "published_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("source_documents.id"), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('russian'::regconfig, text)",
            persisted=True,
        ),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ArticleDigestAnswerRecord(Base):
    """A model's verdict on whether an article's title substantively describes a
    person's own case (an arrest, a sentence, a new charge), not a roundup of several
    people, a foreign-agent/undesirable listing, a support rally, or the like: cached by
    article id, asked once regardless of how many figurants' evidence points to it."""

    __tablename__ = "article_digest_answers"

    article_id: Mapped[int] = mapped_column(
        ForeignKey("parsed_articles.id", ondelete="CASCADE"), primary_key=True
    )
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    relevant: Mapped[bool] = mapped_column(Boolean, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ArticleSentenceReadingRecord(Base):
    """That a model read an article for court sentences, and by which prompt: an article
    it found none in is not read again."""

    __tablename__ = "article_sentence_readings"

    article_id: Mapped[int] = mapped_column(
        ForeignKey("parsed_articles.id", ondelete="CASCADE"), primary_key=True
    )
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ArticleSentenceRecord(Base):
    """One person's sentence as one article tells it (`entities.sentences`): where, what
    punishment, for what. The same sentence told by five articles is five rows;
    `entities.sentence_cases` folds them into one case."""

    __tablename__ = "article_sentences"

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(
        ForeignKey("parsed_articles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The name or the description, as the model gave it.
    person: Mapped[str] = mapped_column(Text, nullable=False)
    # «фамилия имя» folded, when the article gives both; None for a person it does not name.
    person_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # A subject of `entities.regions`; «» when the article does not tell.
    region: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # 0: not told.
    months: Mapped[int] = mapped_column(Integer, nullable=False)
    fine_rub: Mapped[int] = mapped_column(BigInteger, nullable=False)
    in_absentia: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # «2026-09-24», «2024-03», «2024» or «»: as exact as the article is.
    sentenced_on: Mapped[str] = mapped_column(String(10), nullable=False)
    articles: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_text: Mapped[str] = mapped_column(Text, nullable=False)
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    # A person said the row is wrong: it is in no count.
    hidden: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))


class ChatQuestionRecord(Base):
    """A question asked on «Спросить» (`entities.ask`): what was asked, which counts the
    model called for, what they gave, what it answered and what it cost."""

    __tablename__ = "chat_questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    asked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    # [{call, result}, …]: each count the model called for with what it gave.
    calls: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    # answered / refused / failed.
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False)
