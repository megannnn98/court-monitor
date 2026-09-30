"""Reference lists a person keeps by hand in Airtable and syncs into the database.

These are not derived from the articles: they are what the operator wrote down. Airtable
is only the editing surface; PostgreSQL is what the pipeline reads, and the pipeline
never asks Airtable for anything.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base

# Nothing here is derived: every row was written by a person, in Airtable or in the
# console, and survives every rebuild of the tables the pipeline derives.


class AirtableKnownPersonRecord(Base):
    """A person already found and checked by hand: «мы этого человека уже знаем».

    Kept apart from `persons` on purpose: that table is the entity resolution's output
    and is rebuilt as a whole, while this list is an operator's own.
    """

    __tablename__ = "airtable_known_persons"
    __table_args__ = (Index("ix_airtable_known_persons_matching_key", "matching_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # The Airtable record id: the one stable link between the two systems.
    external_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(512), nullable=False)
    # Folded as the pipeline folds a person's name, so a row can be compared with it.
    normalized_name: Mapped[str] = mapped_column(String(512), nullable=False)
    matching_key: Mapped[str] = mapped_column(String(255), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # `onupdate` only fires on an UPDATE, so a fresh row needs the server default too.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class CriminalArticleRecord(Base):
    """A criminal article worth watching: «статьи, которые нас интересуют».

    A list of numbers, not of people, and it is kept apart from `parsed_articles`, which
    holds the articles the pipeline has already parsed out of a text. This one is what
    the operator decided to look for, before anything was found.

    A list of its own rather than a column on `sources`: the list is about what is
    charged, not about where it was published, and the two change for different reasons
    and by different hands.
    """

    __tablename__ = "criminal_articles"
    __table_args__ = (Index("ix_criminal_articles_article_key", "article_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # The Airtable record id: the one stable link between the two systems.
    external_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # «Статья 159 УК РФ» as written in the list, kept for the person to read.
    article_text: Mapped[str] = mapped_column(String(512), nullable=False)
    # The same folded to digits alone, so «159» finds «ст. 159» and «159 УК РФ».
    article_key: Mapped[str] = mapped_column(String(64), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ExcludedPersonRecord(Base):
    """An official, and anyone else who must never become a target figurant.

    Step 4 reads the active rows and treats such a person as named in a case, never as
    the one it is opened against.

    The list is called «Должностные лица» everywhere a person sees it — on
    «Справочники», in `officials.csv`, in `AIRTABLE_OFFICIALS_TABLE`. The table kept the
    name it was created under: renaming it needs a migration, and the working tree holds
    another session's uncommitted work in the very files that carry the table name, which
    a commit here would have swept in. So the name is stale on purpose, not forgotten.
    """

    __tablename__ = "excluded_persons"
    __table_args__ = (Index("ix_excluded_persons_normalized_name", "normalized_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # The Airtable record id: the one stable link between the two systems.
    external_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(512), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(512), nullable=False)
    # judge / prosecutor / police / official / lawyer / witness / other.
    category: Mapped[str] = mapped_column(String(64), nullable=False, default="other")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
