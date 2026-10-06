"""A person's work on the articles the junk screen held back (`monitoring.junk_screen`).

Two ways out of the hold. «Мусор»: the article is junk after all; the next purge deletes
it. «Извлечь заново»: after the extraction was fixed, the article is extracted again; a
criminal-case event found releases it (the hold goes, the next rebuild of step 3 takes the
article in), none found keeps it held with a note. Extraction that did not change is not
run again: the same rules on the same text find the same nothing.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from extraction.documents import SqlAlchemyExtractionDocumentRepository
from extraction.events import RuleBasedEventExtractor
from extraction.extractors import RuleBasedEntityExtractor
from extraction.models import ExtractionRunStatus
from extraction.normalizers import RuleBasedMentionNormalizer
from extraction.persistence import SqlAlchemyExtractionPersistence
from extraction.pipeline import ExtractionPipeline
from monitoring.hold_reader import EXTRACTOR_NAME, RELEASED
from monitoring.junk_purge import CRIMINAL_EVENT_TYPES
from monitoring.junk_screen import HELD, JUNK

_CRIMINAL_EVENTS = text(
    """
    SELECT e.event_type FROM extracted_events e
    WHERE e.extraction_run_id = (
        SELECT r.id FROM article_extraction_runs r
        WHERE r.article_id = :article AND r.status = 'succeeded'
        ORDER BY r.id DESC LIMIT 1
    ) AND e.event_type = ANY(:criminal)
    """
)


@dataclass(frozen=True)
class Reextracted:
    released: bool
    note: str


def _to_junk(session: Session, article_id: int, status: str) -> bool:
    updated = session.execute(
        text(
            "UPDATE junk_screen_holds SET status = :junk, decided_at = now(), "
            "note = 'Отмечено как мусор: удалится при следующей очистке.' "
            "WHERE article_id = :article AND status = :status"
        ),
        {"article": article_id, "junk": JUNK, "status": status},
    )
    return bool(updated.rowcount)  # type: ignore[attr-defined]


def mark_junk(session: Session, article_id: int) -> bool:
    """A person's word that a held article is junk; False when it is not held. A released
    one is not held: a page opened before the release must not undo it."""
    return _to_junk(session, article_id, HELD)


def unrelease(session: Session, article_id: int) -> bool:
    """A person's word that an article was released by mistake; False when it is not
    released. It loses the event that kept it: the purge judges an article by its
    events."""
    if not _to_junk(session, article_id, RELEASED):
        return False
    session.execute(
        text(
            "DELETE FROM extracted_events e USING article_extraction_runs r "
            "WHERE r.id = e.extraction_run_id AND r.article_id = :article "
            "AND e.extractor_name = :extractor"
        ),
        {"article": article_id, "extractor": EXTRACTOR_NAME},
    )
    return True


def hold_again(session: Session, article_id: int) -> bool:
    """Undo «Мусор» before the purge ran. The article is to be read anew: what a model
    said of it before goes, or it would be neither read again nor in the work."""
    updated = session.execute(
        text(
            "UPDATE junk_screen_holds SET status = :held, decided_at = NULL, note = '', "
            "reader = NULL, reader_verdict = NULL, reader_event = NULL "
            "WHERE article_id = :article AND status = :junk"
        ),
        {"article": article_id, "junk": JUNK, "held": HELD},
    )
    return bool(updated.rowcount)  # type: ignore[attr-defined]


def _pipeline(session_factory: sessionmaker[Session]) -> ExtractionPipeline:
    # The monitoring's own pipeline (`application.build_monitoring_service`).
    return ExtractionPipeline(
        extractors=[RuleBasedEntityExtractor()],
        normalizers=[RuleBasedMentionNormalizer()],
        event_extractor=RuleBasedEventExtractor(),
        persistence=SqlAlchemyExtractionPersistence(session_factory),
    )


def reextract(
    session_factory: sessionmaker[Session],
    article_id: int,
    *,
    pipeline: ExtractionPipeline | None = None,
) -> Reextracted:
    """Extract a held article again; release it when a criminal-case event is found."""
    pipeline = pipeline or _pipeline(session_factory)
    document = SqlAlchemyExtractionDocumentRepository(session_factory).get_by_article_id(article_id)
    saved = pipeline.run(document)
    if saved.status != ExtractionRunStatus.SUCCEEDED:
        note = f"Повторное извлечение не удалось: {saved.error_message or 'ошибка'}."
    elif saved.skipped_existing:
        note = (
            "Извлечение этой версии уже было: повторять нечего. Исправьте правила "
            "извлечения — новая версия извлечёт статью заново."
        )
    else:
        note = ""
    with session_factory.begin() as session:
        found = session.scalars(
            _CRIMINAL_EVENTS, {"article": article_id, "criminal": list(CRIMINAL_EVENT_TYPES)}
        ).all()
        if found:
            session.execute(
                text("DELETE FROM junk_screen_holds WHERE article_id = :article"),
                {"article": article_id},
            )
            return Reextracted(
                True,
                f"Найдено уголовное событие ({', '.join(sorted(set(found)))}): статья "
                "возвращена в работу, её возьмёт следующая сборка людей.",
            )
        note = note or "Повторное извлечение не нашло уголовного события."
        session.execute(
            text("UPDATE junk_screen_holds SET note = :note WHERE article_id = :article"),
            {"note": note, "article": article_id},
        )
    return Reextracted(False, note)
