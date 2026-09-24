"""An event-level review feed including reports whose target has no extracted name."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.orm import Session

from channel_feed.case_age import CaseAge, CaseTiming, case_timing
from persecution.classifier import POLITICAL_ARTICLES

MOSCOW = ZoneInfo("Europe/Moscow")
_POLITICAL = re.compile(
    r"госизмен|государственн\w+\s+измен|шпионаж|антивоенн|против\s+войны|"
    r"дискредитац\w*\s+(?:армии|вооруженн)|фейк\w*\s+об\s+армии|"
    r"политическ\w+\s+(?:мотив|преслед)|политзаключ|нежелательн\w+\s+организац",
    re.IGNORECASE,
)
# A full stop right after the number ends the sentence («по статье 280.3.»); only a digit
# or «.digit» would mean the number is longer than what was read («2750», «275.1»).
_ARTICLE = re.compile(r"(?:ст\.|стать[еяию]\w*)\s*(\d+(?:\.\d+)*)(?!\d|\.\d)", re.IGNORECASE)
_CRIMINAL = re.compile(
    r"\bУК\b|уголовн|колони[юия]|лишени\w+\s+свобод|госизмен|"
    r"государственн\w+\s+измен|шпионаж",
    re.IGNORECASE,
)
_ADMINISTRATIVE = re.compile(r"КоАП|административн", re.IGNORECASE)
# The criminal code itself or a criminal case: an administrative word next to it is the
# case's history (a repeated picket before ст. 212.1), not the kind of this event.
_CRIMINAL_CODE = re.compile(r"\bУК\b|уголовн\w*\s+дел", re.IGNORECASE)


@dataclass(frozen=True)
class CaseReport:
    event_id: int
    article_id: int
    published: date
    event_type: str
    names: tuple[str, ...]
    timing: CaseTiming
    basis: str
    quotation: str
    title: str
    source: str
    url: str


def review_basis(quotation: str) -> str | None:
    """Local evidence only: source names and other stories in a digest are not evidence."""
    if _ADMINISTRATIVE.search(quotation) and not _CRIMINAL_CODE.search(quotation):
        return None
    articles = set(_ARTICLE.findall(quotation)) & POLITICAL_ARTICLES
    if articles and _CRIMINAL.search(quotation):
        return "УК: " + ", ".join(sorted(articles))
    if _POLITICAL.search(quotation) and _CRIMINAL.search(quotation):
        return "Уголовный контекст и тематические признаки в цитате; требуется проверка"
    return None


_REPORTS_SQL = text("""
    WITH latest AS (
        SELECT DISTINCT ON (r.article_id) r.id, r.article_id
        FROM article_extraction_runs r
        JOIN parsed_articles a ON a.id = r.article_id
        WHERE r.status = 'succeeded'
          AND a.published_at >= :since AND a.published_at < :until
        ORDER BY r.article_id, r.started_at DESC, r.id DESC
    )
    SELECT ev.id, a.id AS article_id, a.published_at, ev.event_type,
           substring(a.text FROM ev.start_offset + 1 FOR ev.end_offset - ev.start_offset)
               AS quotation,
           a.title, s.name AS source, d.canonical_url AS url,
           ARRAY(
               SELECT DISTINCT m.normalized_text
               FROM event_entity_mentions link
               JOIN entity_mentions m ON m.id = link.mention_id
               WHERE link.event_id = ev.id AND link.role = 'target'
                 AND m.entity_type = 'person' AND m.extraction_run_id = ev.extraction_run_id
               ORDER BY m.normalized_text
           ) AS names
    FROM latest r
    JOIN extracted_events ev ON ev.extraction_run_id = r.id
    JOIN parsed_articles a ON a.id = r.article_id
    JOIN source_documents d ON d.id = a.document_id
    JOIN sources s ON s.id = d.source_id
    WHERE ev.event_type IN ('case_opened', 'charge', 'arrest', 'detention', 'sentence', 'fine')
      AND ev.start_offset >= 0 AND ev.end_offset <= char_length(a.text)
      AND ev.end_offset > ev.start_offset
    ORDER BY a.published_at DESC, a.id DESC, ev.start_offset, ev.id
""")


def load_case_reports(
    db: Session,
    *,
    period_start: date,
    period_end: date,
    age: CaseAge | None = None,
    unnamed_only: bool = False,
) -> list[CaseReport]:
    """Read reports without requiring Person, RF snapshot, or a suggested name.

    Rows are reports, not deduplicated cases. Independent reports must not be merged
    just because a name, age or sentence length matches.
    """
    rows = db.execute(
        _REPORTS_SQL,
        {
            "since": datetime.combine(period_start, time.min, MOSCOW),
            "until": datetime.combine(period_end + timedelta(days=1), time.min, MOSCOW),
        },
    ).mappings()
    reports: list[CaseReport] = []
    for row in rows:
        quotation = str(row["quotation"])
        basis = review_basis(quotation)
        if basis is None:
            continue
        names = tuple(row["names"])
        if unnamed_only and names:
            continue
        published = row["published_at"].astimezone(MOSCOW).date()
        timing = case_timing(
            quotation,
            published=published,
            period_start=period_start,
            period_end=period_end,
        )
        if age is not None and timing.age != age:
            continue
        reports.append(
            CaseReport(
                event_id=row["id"],
                article_id=row["article_id"],
                published=published,
                event_type=row["event_type"],
                names=names,
                timing=timing,
                basis=basis,
                quotation=quotation,
                title=row["title"],
                source=row["source"],
                url=row["url"],
            )
        )
    order = {CaseAge.NEW: 0, CaseAge.UPDATE: 1, CaseAge.UNKNOWN: 2}
    return sorted(
        reports, key=lambda row: (order[row.timing.age], -row.published.toordinal(), row.event_id)
    )
