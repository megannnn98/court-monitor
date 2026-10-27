"""The selection funnel: how the publications become «Результат», step by step.

Each stage says how many are left, which step of the pipeline made it, what it dropped
and why, and where to look. The home page shows it whole; «Результат» a short line.
"""

from __future__ import annotations

from html import escape

from web.views.funnel import Funnel, _n


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
