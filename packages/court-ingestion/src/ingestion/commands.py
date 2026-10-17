"""Commands on stored source data, performed in the caller's transaction."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import bindparam, select, text
from sqlalchemy.orm import Session

from ingestion.storage.models import Source


@dataclass(frozen=True)
class SourceDetails:
    external_id: str
    base_url: str
    name: str
    active: bool


@dataclass(frozen=True)
class SourceRefresh:
    updated: int
    unchanged: int
    unknown_urls: tuple[str, ...]


def refresh_source_details(session: Session, updates: Sequence[SourceDetails]) -> SourceRefresh:
    """Refresh registered sources only; a list cannot create an unloadable source.

    Matching is case/trailing-slash insensitive. One existing row can be claimed
    only once per refresh. The caller owns the transaction and logging of unknown URLs.
    """

    def url_key(url: str) -> str:
        return url.strip().rstrip("/").lower()

    by_url: dict[str, list[Source]] = {}
    for row in session.scalars(select(Source)):
        by_url.setdefault(url_key(row.base_url), []).append(row)
    claimed: set[int] = set()
    changed = unchanged = 0
    unknown: list[str] = []
    for update in updates:
        match = next(
            (row for row in by_url.get(url_key(update.base_url), []) if row.id not in claimed),
            None,
        )
        if match is None:
            unknown.append(update.base_url)
            continue
        claimed.add(match.id)
        name = update.name or match.name
        if (match.external_id, match.name, match.active) == (
            update.external_id,
            name,
            update.active,
        ):
            unchanged += 1
        else:
            match.external_id, match.name, match.active = update.external_id, name, update.active
            changed += 1
    return SourceRefresh(changed, unchanged, tuple(unknown))


def discard_articles(
    session: Session, article_ids: Sequence[int], *, content_type: str | None = None
) -> None:
    """Delete articles and wipe documents to discovery tombstones in this transaction.

    PostgreSQL performs the existing FK cascades. The caller captures any processing
    information needed for orphan cleanup before calling this command. No commit is
    issued here: deleting articles and cleaning their orphan people remain atomic.
    """
    documents = list(
        session.scalars(
            text("DELETE FROM parsed_articles WHERE id IN :ids RETURNING document_id").bindparams(
                bindparam("ids", expanding=True)
            ),
            {"ids": list(article_ids)},
        ).all()
    )
    session.execute(
        text(
            "UPDATE source_documents SET raw_content = '', "
            "content_type = coalesce(:content_type, content_type) WHERE id IN :documents"
        ).bindparams(bindparam("documents", expanding=True)),
        {"documents": documents, "content_type": content_type},
    )
