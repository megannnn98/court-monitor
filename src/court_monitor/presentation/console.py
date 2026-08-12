"""Human-readable CLI output built on Rich.

All terminal rendering for the pipeline lives here. Service/source layers stay
UI-independent — they continue to emit structured log events via structlog.
"""

from __future__ import annotations

import os
import sys
import time as _time
from contextlib import contextmanager

from rich.console import Console
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.rule import Rule
from rich.table import Table

_STYLE_SOURCE = "bold cyan"
_STYLE_SUCCESS = "green"
_STYLE_WARN = "yellow"
_STYLE_ERROR = "red"
_STYLE_CURRENT = "cyan"
_STYLE_DIM = "dim"
_STYLE_HEADING = "bold white"
_STYLE_KEY = "dim"
_STYLE_SCORE_HIGH = "green"
_STYLE_SCORE_MEDIUM = "yellow"
_STYLE_EPHEMERAL: dict[str, bool] = {}


def _emoji(status: str) -> str:
    return {"ok": "✓", "warn": "⚠", "error": "✗", "none": "○", "arrow": "→"}.get(status, "→")


class ConsoleReporter:
    """Human-friendly console output for the monitoring pipeline.

    Usage inside the CLI layer::

        reporter = ConsoleReporter(verbose=True)
        reporter.source_started("2zovs", "2-й Западный окружной военный суд")
        ...
        reporter.source_finished("2zovs", stats)
    """

    def __init__(self, *, verbose: bool = False):
        noir = os.environ.get("NO_COLOR", "") != ""
        self._no_color = noir
        self.verbose = verbose
        self._console = Console(no_color=noir, highlight=False)
        self._source_name: str = ""
        self._start_time: float = 0.0

    @property
    def console(self) -> Console:
        return self._console

    # ── source-level ──────────────────────────────────────────────────

    def db_path(self, db_path_str: str) -> None:
        self._console.print(f"База данных: {db_path_str}", style=_STYLE_HEADING)

    def live_mode(self, live: bool) -> None:
        self._console.print(
            f"Режим: {'LIVE' if live else 'fixture'}",
            style=_STYLE_DIM,
        )

    def source_started(self, source_id: str, display_name: str, *, live: bool = False) -> None:
        self._source_name = source_id
        self._start_time = 0.0
        self._start_time = self._now_seconds()
        mode = "LIVE" if live else "fixture"
        self._console.print(
            Rule(
                f"[{_STYLE_SOURCE}]{display_name}",
                style=_STYLE_DIM,
            )
        )
        self._console.print(f"  Источник: {source_id}", style=_STYLE_DIM)
        self._console.print(f"  Режим: {mode}", style=_STYLE_DIM)

    def archive_loaded(self, months_count: int | None = None) -> None:
        self._console.print(f"  {_emoji('ok')} Архив загружен", style=_STYLE_SUCCESS)
        if months_count is not None:
            self._console.print(
                f"  {_emoji('ok')} Найдено месяцев: {months_count}",
                style=_STYLE_SUCCESS,
            )

    def month_started(self, month_str: str) -> None:
        self._console.print(f"  {_emoji('arrow')} {month_str}", style=_STYLE_CURRENT)

    def document_parsed(
        self,
        did: int | str,
        *,
        published: str | None = None,
        title: str | None = None,
        articles: list[str] | None = None,
        names_count: int | None = None,
        relevant: bool = False,
        facts: int | None = None,
        dates_count: int | None = None,
        matched_articles: list[str] | None = None,
        matched_keywords: list[str] | None = None,
    ) -> None:
        if self.verbose:
            self._print_document_verbose(
                did,
                published,
                title,
                facts,
                articles,
                dates_count,
                names_count,
                matched_articles,
                matched_keywords,
            )
        elif relevant:
            self._print_document_compact(did, published, title, articles, names_count)
        else:
            self._console.print(
                f"  {_emoji('none')} did={did}",
                style=_STYLE_DIM,
            )

    def _print_document_compact(
        self,
        did: int | str,
        published: str | None,
        title: str | None,
        articles: list[str] | None,
        names_count: int | None,
    ) -> None:
        date_part = f"  {published}" if published else ""
        line = f"  {_emoji('ok')} did={did}{date_part}"
        self._console.print(line, style=_STYLE_SUCCESS)
        if title:
            self._console.print(f"    {_text_safe(title, 80)}", style=_STYLE_DIM)
        if articles:
            self._console.print(
                f"    Статьи: {', '.join(articles)}",
                style=_STYLE_DIM,
            )
        if names_count is not None and names_count > 0:
            self._console.print(f"    ФИО: {names_count}", style=_STYLE_DIM)

    def _print_document_verbose(  # noqa: PLR0917
        self,
        did: int | str,
        published: str | None,
        title: str | None,
        facts: int | None,
        articles: list[str] | None,
        dates_count: int | None,
        names_count: int | None,
        matched_articles: list[str] | None,
        matched_keywords: list[str] | None,
    ) -> None:
        stat_parts = []
        if facts is not None:
            stat_parts.append(f"facts: {facts}")
        if articles is not None:
            stat_parts.append(f"articles: {len(articles)}")
        if dates_count is not None:
            stat_parts.append(f"dates: {dates_count}")
        if names_count is not None:
            stat_parts.append(f"names: {names_count}")
        stats_str = ", ".join(stat_parts) if stat_parts else ""

        date_part = f" {published}" if published else ""
        self._console.print(
            f"  {_emoji('ok')} Документ #{did}{date_part}  ({stats_str})",
            style=_STYLE_SUCCESS,
        )
        if title:
            self._console.print(f"    заголовок: {_text_safe(title, 80)}", style=_STYLE_DIM)
        if articles:
            self._console.print(f"    статьи: {', '.join(articles)}", style=_STYLE_DIM)
        if matched_articles:
            self._console.print(
                f"    совпавшие статьи: {', '.join(matched_articles)}",
                style=_STYLE_SUCCESS,
            )
        if matched_keywords:
            self._console.print(
                "    ключевые слова:",
                style=_STYLE_SUCCESS,
            )
            for kw in matched_keywords:
                self._console.print(f"      • {kw}", style=_STYLE_DIM)

    # ── search ────────────────────────────────────────────────────────

    def search_started(self, article: str | None = None, date_hint: str | None = None) -> None:
        parts = []
        if article:
            parts.append(f"ст. {article}")
        if date_hint:
            parts.append(f"дата {date_hint}")
        criteria = ", ".join(parts) if parts else "поиск"
        self._console.print(
            f"  {_emoji('arrow')} Поиск: {criteria}",
            style=_STYLE_CURRENT,
        )

    def search_results(self, count: int) -> None:
        style = _STYLE_SUCCESS if count > 0 else _STYLE_WARN
        self._console.print(
            f"  {_emoji('ok')} Найдено результатов: {count}",
            style=style,
        )

    def search_error(self, strategy: str, error: str) -> None:
        self._console.print(
            f"    {_emoji('warn')} поиск ({strategy}): {error}",
            style=_STYLE_WARN,
        )

    def candidate_checking(self, case_ref: str) -> None:
        self._console.print(
            f"  {_emoji('arrow')} Проверка карточки {case_ref}",
            style=_STYLE_CURRENT,
        )

    def candidate_found(
        self,
        case_ref: str,
        score: float,
        signals: list[str] | None = None,
        candidate_id: int | None = None,
        verified: bool = False,
    ) -> None:
        score_style = _STYLE_SCORE_HIGH if score >= 0.8 else _STYLE_SCORE_MEDIUM
        self._console.print(
            f"  {_emoji('ok')} Кандидат: {case_ref}  score={score:.2f}",
            style=score_style,
        )
        if signals:
            self._console.print("    совпало:", style=_STYLE_DIM)
            for s in signals:
                self._console.print(f"      • {s}", style=_STYLE_DIM)
        if candidate_id:
            cid_msg = "кандидат" if not verified else "подтверждён"
            self._console.print(
                f"    {cid_msg} #{candidate_id}",
                style=_STYLE_DIM,
            )

    def no_match(self) -> None:
        self._console.print(
            f"  {_emoji('none')} Подходящее дело не найдено",
            style=_STYLE_DIM,
        )

    def card_parse_failed(self, error: str) -> None:
        self._console.print(
            f"    {_emoji('warn')} ошибка разбора карточки: {error}",
            style=_STYLE_WARN,
        )

    def card_transport_failed(self, error: str, *, permanent: bool = False) -> None:
        marker = "error" if permanent else "warn"
        self._console.print(
            f"    {_emoji(marker)} карточка: {error}",
            style=_STYLE_ERROR if permanent else _STYLE_WARN,
        )

    # ── review ────────────────────────────────────────────────────────

    def review_candidate(
        self,
        candidate_id: int,
        case_ref: str,
        score: float,
    ) -> None:
        self._console.print(
            f"  {_emoji('ok')} Требуется проверка оператора",
            style=_STYLE_WARN,
        )
        self._console.print(f"    кандидат #{candidate_id}", style=_STYLE_DIM)
        self._console.print(f"    дело: {case_ref}", style=_STYLE_DIM)
        self._console.print(f"    score: {score:.2f}", style=_STYLE_DIM)

    # ── stats ─────────────────────────────────────────────────────────

    def source_summary(
        self,
        *,
        fetched: int = 0,
        new_documents: int = 0,
        duplicates: int = 0,
        parsed: int = 0,
        irrelevant: int = 0,
        failed: int = 0,
        blocked: int = 0,
        elapsed: float | None = None,
    ) -> None:
        self._console.print()
        self._console.print(
            f"  [{_STYLE_SUCCESS}]Итог[/{_STYLE_SUCCESS}]",
        )
        table = Table.grid(padding=(0, 2))
        table.add_column(style=_STYLE_KEY, justify="right")
        table.add_column()

        lines = [
            ("загружено", str(fetched)),
            ("новых документов", str(new_documents)),
            ("дублей", str(duplicates)),
        ]
        if parsed:
            lines.append(("распарсено", str(parsed)))
        if irrelevant:
            lines.append(("нерелевантных", str(irrelevant)))
        if blocked:
            lines.append(("заблокировано", str(blocked)))
        if failed:
            lines.append(("ошибок", str(failed)))

        for label, value in lines:
            table.add_row(label, value)
        self._console.print(table, style=_STYLE_DIM)

        if elapsed is not None:
            self._console.print(
                f"  {_emoji('ok')} Готово за {elapsed:.1f} с",
                style=_STYLE_SUCCESS,
            )

    def court_summary(
        self,
        court_name: str,
        *,
        processed: int = 0,
        review_created: int = 0,
        no_match: int = 0,
        temporary_failures: int = 0,
        failed: int = 0,
    ) -> None:
        self._console.print(
            f"  [{_STYLE_SOURCE}]{court_name}[/{_STYLE_SOURCE}] — итог",
        )
        table = Table.grid(padding=(0, 2))
        table.add_column(style=_STYLE_KEY, justify="right")
        table.add_column()
        for label, value in [
            ("обработано", str(processed)),
            ("review", str(review_created)),
            ("no match", str(no_match)),
            ("temporary failure", str(temporary_failures)),
            ("ошибок", str(failed)),
        ]:
            table.add_row(label, value)
        self._console.print(table, style=_STYLE_DIM)

    def grand_total(
        self,
        *,
        sources: int,
        new_documents: int,
        review_candidates: int = 0,
        errors: int = 0,
        temporary_errors: int = 0,
    ) -> None:
        self._console.print(
            Rule(
                f"[{_STYLE_HEADING}]ИТОГ[/{_STYLE_HEADING}]",
                style=_STYLE_DIM,
            )
        )
        table = Table.grid(padding=(0, 2))
        table.add_column(style=_STYLE_KEY, justify="right")
        table.add_column()
        for label, value in [
            ("источников", str(sources)),
            ("новых документов", str(new_documents)),
            ("кандидатов review", str(review_candidates)),
            ("временных ошибок", str(temporary_errors)),
            ("ошибок", str(errors)),
        ]:
            table.add_row(label, value)
        self._console.print(table, style=_STYLE_DIM)

    def match_stats(
        self, created: int, already_existed: int, no_candidates: int, errors: int
    ) -> None:
        self._console.print()
        self._console.print(f"  [{_STYLE_SOURCE}]Совпадения[/{_STYLE_SOURCE}]", style=_STYLE_DIM)
        table = Table.grid(padding=(0, 2))
        table.add_column(style=_STYLE_KEY, justify="right")
        table.add_column()
        for label, value in [
            ("создано", str(created)),
            ("уже было", str(already_existed)),
            ("без кандидата", str(no_candidates)),
            ("ошибок", str(errors)),
        ]:
            table.add_row(label, value)
        self._console.print(table, style=_STYLE_DIM)

    # ── errors / warnings ─────────────────────────────────────────────

    def error(self, message: str, *, exception_type: str | None = None) -> None:
        exc = f" {exception_type}" if exception_type else ""
        self._console.print(
            f"  {_emoji('error')}{exc}: {message}",
            style=_STYLE_ERROR,
        )

    def warning(self, message: str) -> None:
        self._console.print(
            f"  {_emoji('warn')} {message}",
            style=_STYLE_WARN,
        )

    def detail(self, key: str, value: str) -> None:
        self._console.print(f"    {key}: {value}", style=_STYLE_DIM)

    def empty_group(self, note: str) -> None:
        self._console.print(f"  ({note})", style=_STYLE_DIM)

    def blank_line(self) -> None:
        self._console.print()

    # ── spinner (for async-looking operations) ────────────────────────

    @contextmanager
    def spinner(self, description: str):
        """Show a transient Rich spinner while a blocking operation runs."""
        with Progress(
            SpinnerColumn("dots"),
            TextColumn("[cyan]{task.description}"),
            TimeElapsedColumn(),
            console=self._console,
            transient=True,
        ) as progress:
            task = progress.add_task(description, total=None)
            try:
                yield
                progress.update(task, description=f"{_emoji('ok')} {description}")
            except Exception:
                progress.update(task, description=f"{_emoji('error')} {description}")
                raise

    def spinner_done(self, label: str) -> None:
        self._console.print(f"  {_emoji('ok')} {label}", style=_STYLE_SUCCESS)

    def spinner_failed(self, label: str) -> None:
        self._console.print(f"  {_emoji('error')} {label}", style=_STYLE_ERROR)

    # ── section headings ──────────────────────────────────────────────

    def heading(self, text: str) -> None:
        self._console.print(Rule(f"[{_STYLE_SOURCE}]{text}", style=_STYLE_DIM))

    # ── helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _now_seconds() -> float:
        return _time.monotonic()


def _text_safe(text: str, max_len: int) -> str:
    """Truncate plain text for display purposes."""
    t = text.replace("\n", " ").replace("\r", " ").strip()
    if len(t) > max_len:
        return t[: max_len - 3] + "..."
    return t


def is_tty() -> bool:
    return sys.stdout.isatty()
