"""The selection funnel: how the publications become «Результат», step by step.

Each stage says how many are left, which step of the pipeline made it, what it dropped
and why, and where to look. The home page shows it whole; «Результат» a short line.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from html import escape

from sqlalchemy.orm import Session

from analysis import read as analysis_read
from ingestion import read as ingestion_read
from processing import read as identity
from processing.junk.purge import CRIMINAL_EVENT_TYPES, EXPIRED_CONTENT_TYPE
from processing.junk.screen import HELD
from processing.roles.officials import OFFICIAL_KINDS


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
    # Each owner counts its own: the documents and dates (ingestion), steps 2–4
    # (processing), the list and the verdicts (analysis).
    documents = ingestion_read.live_document_count(db, expired_type=EXPIRED_CONTENT_TYPE)
    since, until = ingestion_read.published_span(db)
    selection = identity.selection_counts(
        db, criminal=CRIMINAL_EVENT_TYPES, held=HELD, officials=OFFICIAL_KINDS
    )
    criminal_publications, held = selection.criminal_publications, selection.held
    entities, figurants, officials = selection.groups, selection.figurants, selection.officials
    verdicts = analysis_read.verdict_counts(db)
    listed, political, criminal, unclear = (
        verdicts.listed,
        verdicts.political,
        verdicts.criminal,
        verdicts.unclear,
    )
    stages = [
        Stage("1", "Публикаций скачано", documents, "", "/ui/runs"),
        Stage(
            "2",
            "С уголовным делом",
            criminal_publications,
            # A held publication is kept for a person to look at: neither a case nor dropped.
            f"отсеяно {_n(documents - criminal_publications - held)} без уголовного дела "
            "(штрафы, прочие новости)"
            + (f"; {_n(held)} отложены на проверку и ни туда, ни сюда не входят" if held else ""),
            "/ui/runs",
        ),
        Stage(
            "3",
            "Людей в них",
            entities,
            # People are collected from the publications with a case only (step 3 reads no
            # other), so the number is the one of the stage above.
            f"из {_n(criminal_publications)} публикаций; одно имя в любом падеже — один человек, "
            "спорные — в «Спорных случаях»",
            "/ui/entities?rf=all&figurants=all",
        ),
        Stage(
            "4",
            "Фигуранты уголовных дел",
            figurants,
            f"отсеяно {_n(entities - figurants)}: только упомянуты, административное дело, "
            f"дело не в России, должностные лица ({_n(officials)}), не ясно",
            "/ui/entities",
        ),
        # The list is checked in the final step too; it confirms who a person is and drops
        # nobody.
        Stage(
            "5",
            "Политические дела — Результат",
            political,
            f"отсеяно {_n(criminal)} с обычной уголовщиной и {_n(unclear)} неясных; перечень РФМ "
            f"никого не отсеял — {_n(listed)} найдены в нём, их личность подтверждают дата "
            "рождения и место",
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
    return f"""<section class="band funnel" id="funnel">
  <h2>Воронка отбора</h2>
  <p class="funnel-period"><b>За всё время:</b> {escape(whole.period)}. Период на
  «Результате» выбирается отдельно.</p>
  <p class="muted">Шаги 1–5 по очереди сужают поток: из скачанных публикаций — к людям с
  политическими уголовными делами. Перечень Росфинмониторинга никого не отсеивает, он
  подтверждает личность. Нажмите на ступень, чтобы её посмотреть.</p>
  {rows}
</section>"""


def funnel_line(whole: Funnel) -> str:
    """The funnel in one line, for «Результат»."""
    parts = " → ".join(f"{_n(stage.count)} {escape(stage.label.lower())}" for stage in whole.stages)
    return (
        f'<p class="muted funnel-line">Воронка за всё время ({escape(whole.period)}): {parts}. '
        '<a href="/ui/runs#funnel">Подробнее</a></p>'
    )
