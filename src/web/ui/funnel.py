"""The selection funnel: how the publications become «Результат», step by step.

Each stage says how many are left, which step of the pipeline made it, what it dropped
and why, and where to look. The home page shows it whole; «Результат» a short line.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from html import escape

from sqlalchemy import text
from sqlalchemy.orm import Session

from entities.officials import OFFICIAL_KINDS
from monitoring.junk_purge import CRIMINAL_EVENT_TYPES

_COUNTS = text(
    """
    SELECT
      (SELECT count(*) FROM source_documents),
      (SELECT count(*) FROM parsed_articles),
      (SELECT count(*) FROM entity_groups),
      (SELECT count(*) FROM entity_groups g WHERE NOT EXISTS (
          SELECT 1 FROM entity_group_rf_matches m WHERE m.group_id = g.id AND m.level = 'full')),
      (SELECT count(*) FROM entity_group_roles WHERE role = 'figurant'),
      (SELECT count(*) FROM entity_group_roles WHERE kind = ANY(:officials)),
      (SELECT count(*) FROM entity_group_politics WHERE verdict = 'political'),
      (SELECT count(*) FROM entity_group_politics WHERE verdict = 'criminal'),
      (SELECT count(*) FROM entity_group_politics WHERE verdict = 'unclear'),
      (SELECT min(published_at) FROM parsed_articles),
      (SELECT max(published_at) FROM parsed_articles)
    """
)
_CRIMINAL_PUBLICATIONS = text(
    """
    SELECT count(DISTINCT r.article_id) FROM article_extraction_runs r
    JOIN extracted_events e ON e.extraction_run_id = r.id
    WHERE r.status = 'succeeded' AND e.event_type = ANY(:criminal)
    """
)


@dataclass(frozen=True)
class Stage:
    step: str
    label: str
    count: int
    # What the stage dropped from the one above, and why; empty for the first.
    dropped: str
    href: str


@dataclass(frozen=True)
class Funnel:
    stages: list[Stage]
    # The dates of the first and the latest publication: the funnel counts them all.
    since: datetime | None
    until: datetime | None

    @property
    def period(self) -> str:
        if self.since is None or self.until is None:
            return "публикаций пока нет"
        return f"публикации с {self.since:%d.%m.%Y} по {self.until:%d.%m.%Y}"


def _n(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def funnel(db: Session) -> Funnel:
    (
        documents,
        publications,
        entities,
        off_list,
        figurants,
        officials,
        political,
        criminal,
        unclear,
        since,
        until,
    ) = db.execute(_COUNTS, {"officials": sorted(OFFICIAL_KINDS)}).one()
    criminal_publications = (
        db.scalar(_CRIMINAL_PUBLICATIONS, {"criminal": list(CRIMINAL_EVENT_TYPES)}) or 0
    )
    stages = [
        Stage("1", "Публикаций скачано", documents, "", "/ui/management"),
        Stage(
            "2",
            "С уголовным делом",
            criminal_publications,
            f"отсеяно {_n(documents - criminal_publications)} без уголовного дела "
            "(штрафы, прочие новости)",
            "/ui/management",
        ),
        Stage(
            "3",
            "Людей в них",
            entities,
            f"из {_n(publications)} публикаций; одно имя в любом падеже — один человек, "
            "спорные — в «Спорных случаях»",
            "/ui/entities?rf=all&figurants=all",
        ),
        Stage(
            "4",
            "Нет в перечне РФМ",
            off_list,
            f"отсеяно {_n(entities - off_list)} — они в перечне Росфинмониторинга",
            "/ui/entities?rf=hide&figurants=all",
        ),
        Stage(
            "5",
            "Фигуранты уголовных дел",
            figurants,
            f"отсеяно {_n(off_list - figurants)}: только упомянуты, административное дело, "
            f"должностные лица ({_n(officials)}), не ясно",
            "/ui/entities",
        ),
        Stage(
            "6",
            "Политические дела — Результат",
            political,
            f"отсеяно {_n(criminal)} с обычной уголовщиной и {_n(unclear)} неясных",
            "/ui/political",
        ),
    ]
    return Funnel(stages, since, until)


def funnel_html(whole: Funnel) -> str:
    """The funnel whole: a bar per stage, narrower as it goes, the result last."""
    stages = whole.stages
    top = max(stages[0].count, 1)
    rows = "".join(
        f'<a class="funnel-stage{" result" if index == len(stages) - 1 else ""}" '
        f'href="{escape(stage.href, quote=True)}">'
        f'<span class="funnel-step">{stage.step}</span>'
        f'<span class="funnel-bar" style="width:{max(stage.count / top * 100, 12):.0f}%">'
        f"<b>{_n(stage.count)}</b> {escape(stage.label)}</span>"
        f'<span class="funnel-dropped muted">{escape(stage.dropped)}</span></a>'
        for index, stage in enumerate(stages)
    )
    return f"""<section class="band funnel">
  <h2>Воронка отбора</h2>
  <p class="funnel-period"><b>За всё время:</b> {escape(whole.period)}. Период на
  «Результате» выбирается отдельно.</p>
  <p class="muted">Шаги 1–6 ниже по очереди сужают поток: из скачанных публикаций — к людям с
  политическими уголовными делами, которых нет в перечне. Нажмите на ступень, чтобы её
  посмотреть.</p>
  {rows}
</section>"""


def funnel_line(whole: Funnel) -> str:
    """The funnel in one line, for «Результат»."""
    parts = " → ".join(f"{_n(stage.count)} {escape(stage.label.lower())}" for stage in whole.stages)
    return (
        f'<p class="muted funnel-line">Воронка за всё время ({escape(whole.period)}): {parts}. '
        '<a href="/ui/management">Подробнее</a></p>'
    )
