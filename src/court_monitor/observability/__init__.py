"""Structured JSON logging (structlog) with correlation_id (contextvars).

Supports three output modes:
  * ``"json"`` — raw structured JSON per line (machine-readable, current default).
  * ``"human"`` — structlog is suppressed; presentation is handled by
    :class:`~court_monitor.presentation.console.ConsoleReporter`.
  * ``"verbose"`` — structlog events are rendered as human-readable lines
    (with event name, correlation_id short-id, and key fields), interspersed
    with ConsoleReporter output.
"""

from __future__ import annotations

import collections.abc
import sys
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, TextIO, cast

import structlog

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def get_correlation_id() -> str | None:
    return _correlation_id.get()


def new_correlation_id() -> str:
    cid = uuid.uuid4().hex
    _correlation_id.set(cid)
    return cid


@contextmanager
def correlation_scope(cid: str | None = None):
    """Bind a correlation_id for the duration of the block (restores previous)."""
    token = _correlation_id.set(cid or uuid.uuid4().hex)
    try:
        yield _correlation_id.get()
    finally:
        _correlation_id.reset(token)


def configure_logging(level: str = "INFO") -> None:
    """Configure structlog for raw JSON output (machine-readable mode).

    Kept for backwards compatibility. New code should prefer
    :func:`configure_logging_with_mode`.
    """
    _configure_json(level)


def configure_logging_with_mode(
    level: str = "INFO",
    *,
    mode: str = "json",
    json_file: TextIO | None = None,
) -> None:
    """Configure structlog for the given output mode.

    Parameters:
        level: Minimum log level (DEBUG/INFO/WARNING/ERROR).
        mode:
            * ``"json"`` — raw JSON lines to stdout (machine-readable).
            * ``"human"`` — suppress structlog output entirely.
            * ``"verbose"`` — human-readable event lines to stdout.
        json_file: When set, write JSON events to this file regardless of
                   ``mode`` (structured logging sidecar).
    """
    _json_sidecar_state[0] = json_file

    if mode == "json":
        _configure_json(level)
    elif mode == "verbose":
        _configure_verbose(level)
    else:
        _configure_silent(level)


def _configure_json(level: str) -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _inject_correlation_id,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            _json_with_sidecar,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(_level_to_int(level)),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def _configure_verbose(level: str) -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _inject_correlation_id,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            _human_renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(_level_to_int(level)),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def _configure_silent(level: str) -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _inject_correlation_id,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            _null_renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(_level_to_int(level)),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(sys.stderr),
        cache_logger_on_first_use=True,
    )


def _inject_correlation_id(
    _logger: object, _method_name: str, event_dict: collections.abc.MutableMapping[str, Any]
) -> collections.abc.MutableMapping[str, Any]:
    cid = _correlation_id.get()
    if cid:
        event_dict["correlation_id"] = cid
    return event_dict


def _level_to_int(level: str) -> int:
    level = level.upper()
    return {
        "DEBUG": 10,
        "INFO": 20,
        "WARNING": 30,
        "ERROR": 40,
        "CRITICAL": 50,
    }.get(level, 20)


# ── JSON renderer with optional sidecar ──────────────────────────────

_json_sidecar_state: list[TextIO | None] = [None]

_JSON_RENDERER = structlog.processors.JSONRenderer(ensure_ascii=False)


def _json_with_sidecar(
    _logger: object, _method_name: str, event_dict: collections.abc.MutableMapping[str, Any]
) -> str:
    line = _JSON_RENDERER(_logger, _method_name, event_dict)
    sidecar = _json_sidecar_state[0]
    if sidecar is not None:
        sidecar.write(cast(str, line))
        sidecar.write("\n")
        sidecar.flush()
        raise structlog.DropEvent
    return cast(str, line)


# ── human-friendly renderer (verbose mode) ────────────────────────────


def _human_renderer(  # noqa: PLR0911
    _logger: object,
    method_name: str,
    event_dict: collections.abc.MutableMapping[str, Any],
) -> str:
    """Render a structlog event as a human-friendly line for --verbose mode."""
    event = event_dict.get("event", "unknown")
    level = event_dict.get("level", "info")
    cid = event_dict.get("correlation_id", "")
    cid_short = cid[:12] if cid else ""

    extra = dict(event_dict)
    for key in ("event", "level", "timestamp", "correlation_id"):
        extra.pop(key, None)

    prefix = _level_prefix(level)
    cid_part = f" [{cid_short}]" if cid_short and cid_short.strip() else ""

    renderers = {
        "pipeline.parsed": _render_parsed,
        "orchestrator.start": _render_orch_start,
        "orchestrator.extracted": _render_extracted,
        "orchestrator.search_results": _render_search_results,
        "orchestrator.complete": _render_orch_complete,
        "progressive_search.summary": lambda *_: "",
        "sudrf.search.start": _render_sudrf_search,
        "sudrf.search.results": _render_sudrf_results,
        "sudrf.crawler.months_found": _render_months,
        "sudrf.crawler.fetching": _render_fetching,
    }

    render_fn = renderers.get(event)
    if render_fn is not None:
        return render_fn(prefix, cid_part, extra)

    return _render_generic(prefix, cid_part, method_name, event, extra)


def _level_prefix(level: str) -> str:
    return {
        "error": "✗",
        "critical": "✗",
        "warning": "⚠",
        "info": "→",
        "debug": "·",
    }.get(level, "→")


def _render_generic(
    prefix: str, cid_part: str, method_name: str, event: str, extra: dict[str, Any]
) -> str:
    kv = " ".join(f"{k}={_fmt_val(v)}" for k, v in extra.items())
    return f"  {prefix}{cid_part} {event} {kv}"


def _render_parsed(prefix: str, cid_part: str, extra: dict[str, Any]) -> str:
    did = extra.get("document_id", "?")
    facts = extra.get("facts", 0)
    relevant = extra.get("relevant", False)
    articles = extra.get("articles", 0)
    dates = extra.get("dates", 0)
    names = extra.get("names", 0)
    matched_arts = extra.get("matched_articles", [])
    matched_kws = extra.get("matched_keywords", [])

    rel = "релевантен" if relevant else "нерелевантен"
    matched = []
    if matched_arts:
        matched.append(f"статьи={matched_arts}")
    if matched_kws:
        matched.append(f"keywords={matched_kws}")

    line = (
        f"  {prefix}{cid_part} did={did}: {rel}, "
        f"facts={facts}, articles={articles}, dates={dates}, names={names}"
    )
    lines = [line]
    for m in matched:
        lines.append(f"    {m}")
    return "\n".join(lines)


def _render_orch_start(prefix: str, cid_part: str, extra: dict[str, Any]) -> str:
    did = extra.get("document_id", "?")
    court = extra.get("court", "?")
    return f"  {prefix}{cid_part} Обработка дела: did={did}, court={court}"


def _render_extracted(prefix: str, cid_part: str, extra: dict[str, Any]) -> str:
    did = extra.get("document_id", "?")
    article = extra.get("article", "")
    rdate = extra.get("result_date", "")
    etype = extra.get("event_type", "")
    person = extra.get("person_name", "")

    parts = [f"did={did}"]
    if article:
        parts.append(f"article={article}")
    if rdate:
        parts.append(f"result_date={rdate}")
    if etype:
        parts.append(f"event_type={etype}")
    if person:
        parts.append(f"person={person}")
    return f"  {prefix}{cid_part} Извлечено: {', '.join(parts)}"


def _render_search_results(prefix: str, cid_part: str, extra: dict[str, Any]) -> str:
    did = extra.get("document_id", "?")
    count = extra.get("count", 0)
    attempts = extra.get("attempts", 0)
    return f"  {prefix}{cid_part} did={did}: найдено дел={count} (попыток поиска={attempts})"


def _render_orch_complete(prefix: str, cid_part: str, extra: dict[str, Any]) -> str:
    did = extra.get("document_id", "?")
    matched = extra.get("matched", 0)
    evaluated = extra.get("evaluated", 0)
    return f"  {prefix}{cid_part} did={did}: завершено, matched={matched}, evaluated={evaluated}"


def _render_sudrf_search(prefix: str, cid_part: str, extra: dict[str, Any]) -> str:
    return ""


def _render_sudrf_results(prefix: str, cid_part: str, extra: dict[str, Any]) -> str:
    return ""


def _render_months(prefix: str, cid_part: str, extra: dict[str, Any]) -> str:
    count = extra.get("count", 0)
    return f"  {prefix}{cid_part} Найдено месяцев: {count}"


def _render_fetching(prefix: str, cid_part: str, extra: dict[str, Any]) -> str:
    month = extra.get("month", "?")
    return f"  {prefix}{cid_part} Загружаю: {month}"


def _fmt_val(v: object) -> str:
    if isinstance(v, list):
        return f"[{', '.join(str(x) for x in v[:3])}{'...' if len(v) > 3 else ''}]" if v else "[]"
    if isinstance(v, bool):
        return "да" if v else "нет"
    return str(v)


# ── null renderer ─────────────────────────────────────────────────────


def _null_renderer(
    _logger: object,
    _method_name: str,
    event_dict: collections.abc.MutableMapping[str, Any],
) -> str:
    """Drop the event — structured logging sidecar captures it if configured."""
    sidecar = _json_sidecar_state[0]
    if sidecar is not None:
        line = _JSON_RENDERER(_logger, _method_name, event_dict)
        sidecar.write(cast(str, line))
        sidecar.write("\n")
        sidecar.flush()
    raise structlog.DropEvent


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
