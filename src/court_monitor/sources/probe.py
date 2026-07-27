"""Availability probing for registry sources.

A probe is a single polite HTTP request (the same one the adapter would make)
classified into a coarse status. A failed probe NEVER marks a source as
deleted — only ``temporarily_unavailable`` (or a more specific reason) so a
transient outage does not erase a registry entry.
"""

from __future__ import annotations

from dataclasses import dataclass

from court_monitor.normalization import canonicalize_url
from court_monitor.sources.http_client import HttpClient

# Status vocabulary — see spec §"Проверка доступности".
AVAILABLE = "available"
TEMPORARILY_UNAVAILABLE = "temporarily_unavailable"
BLOCKED = "blocked"
REQUIRES_AUTH = "requires_auth"
CAPTCHA = "captcha"
UNSUPPORTED = "unsupported"
INVALID_URL = "invalid_url"
PARSE_ERROR = "parse_error"


@dataclass(frozen=True)
class ProbeResult:
    status: str
    http_status: int
    note: str


def probe_source(entry, *, client: HttpClient | None = None) -> ProbeResult:
    """Probe one registry entry. ``entry`` exposes ``source_type``, ``url``, ``username``."""
    source_type = getattr(entry, "source_type", "website")
    username = getattr(entry, "username", None)
    url = getattr(entry, "url", "") or ""

    if source_type == "telegram":
        if not username:
            return ProbeResult(INVALID_URL, 0, "Нет username канала")
        probe_url = f"https://t.me/s/{username.lstrip('@')}"
    elif source_type in {"website", "rss"}:
        canon = canonicalize_url(url)
        if canon is None:
            return ProbeResult(INVALID_URL, 0, "URL неразборчивый или пустой")
        probe_url = canon
        # website/rss adapters are not implemented yet in this stage.
        return ProbeResult(UNSUPPORTED, 0, "Требуется отдельный адаптер")
    else:
        return ProbeResult(UNSUPPORTED, 0, f"Тип «{source_type}» не поддерживается")

    return _http_probe(probe_url, client, source_type=source_type)


def _http_probe(url: str, client: HttpClient | None, *, source_type: str) -> ProbeResult:
    owns_client = client is None
    c = client or HttpClient()
    try:
        resp = c.get(url)
    except Exception as exc:  # noqa: BLE001 - surfaced as a status, never raised
        return ProbeResult(TEMPORARILY_UNAVAILABLE, 0, f"Сеть/DNS: {type(exc).__name__}")
    finally:
        if owns_client:
            c.close()

    body = resp.text or ""
    result = _classify_http_response(resp, body, source_type=source_type)
    if result is not None:
        return result
    if source_type == "telegram":
        return _verify_telegram_posts(body, resp.status)
    return ProbeResult(AVAILABLE, resp.status, "Ответ получен")


def _classify_http_response(resp, body: str, *, source_type: str) -> ProbeResult | None:
    """Map a non-success health/HTTP outcome to a probe status (or None to continue)."""
    del source_type  # reserved for future per-type tweaks
    if resp.health == "timeout":
        return ProbeResult(TEMPORARILY_UNAVAILABLE, resp.status, "Тайм-аут запроса")

    status = resp.status
    if resp.health == "blocked":
        return _blocked_result(body, status)
    if status >= 500:
        return ProbeResult(TEMPORARILY_UNAVAILABLE, status, f"HTTP {status}")
    if status >= 400:
        return _client_error_result(status)
    return None


def _blocked_result(body: str, status: int) -> ProbeResult:
    lowered = body.lower()
    if "captcha" in lowered or "проверка безопасности" in lowered:
        return ProbeResult(CAPTCHA, status, "Обнаружена CAPTCHA")
    return ProbeResult(BLOCKED, status, f"HTTP {status} — блокировка/антибот")


def _client_error_result(status: int) -> ProbeResult:
    if status in (401, 407):
        return ProbeResult(REQUIRES_AUTH, status, f"HTTP {status} — требуется авторизация")
    return ProbeResult(TEMPORARILY_UNAVAILABLE, status, f"HTTP {status}")


def _verify_telegram_posts(body: str, status: int) -> ProbeResult:
    if not body:
        return ProbeResult(PARSE_ERROR, status, "Пустой ответ")
    try:
        from court_monitor.parsers.telegram_post import parse_channel_preview  # noqa: PLC0415
    except Exception:  # pragma: no cover - import guard
        return ProbeResult(AVAILABLE, status, "HTML получен")
    posts = parse_channel_preview(body)
    if not posts:
        return ProbeResult(
            TEMPORARILY_UNAVAILABLE, status, "Превью без постов (возможно, канал ограничен)"
        )
    return ProbeResult(AVAILABLE, status, f"HTML получен, постов: {len(posts)}")
