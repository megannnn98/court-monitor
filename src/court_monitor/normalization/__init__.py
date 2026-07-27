"""FIO / text normalization — spec §8.

Stage-1 implementation: case-fold, ё→е, collapse dashes/spaces/apostrophes,
trim. The full feature set (cases, transliteration, OCR errors, phonetic,
aliases, double surnames, former surnames) is layered on in Etap 6. The
original spelling is ALWAYS preserved separately — normalization never
overwrites the source value.
"""

from __future__ import annotations

import re
from urllib.parse import urldefrag, urlsplit, urlunsplit

from court_monitor.sources.base import normalize_text as normalize_text_ws  # noqa: F401

_FIO_MAP = str.maketrans({"ё": "е", "Ё": "е", "-": " ", "'": " ", "`": " "})
_WS_RE = re.compile(r"\s+")

# Query params that carry no identity (tracking / ephemeral tokens). Stripped
# during URL canonicalisation so the same document does not dedup-split.
_NOISE_QUERY_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "ref",
        "referrer",
        "fbclid",
        "gclid",
        "_hsenc",
        "_hsmi",
        "mc_cid",
        "mc_eid",
        "yclid",
        "from",
    }
)


def normalize_fio(value: str) -> str:
    if not value:
        return ""
    return _WS_RE.sub(" ", value.translate(_FIO_MAP)).strip().lower()


def normalize_fio_parts(value: str) -> list[str]:
    return [p for p in normalize_fio(value).split(" ") if p]


def canonicalize_url(url: str | None) -> str | None:
    """Return a stable canonical form of ``url`` for deduplication.

    * trims surrounding whitespace;
    * adds a default ``https`` scheme when missing (``//host`` or ``host``);
    * drops the fragment;
    * removes well-known tracking query parameters;
    * lower-cases the host;
    * strips trailing slash on the path (except for the root ``/``).

    Returns ``None`` for empty / unparsable input. Never raises.
    """
    if not url:
        return None
    raw = url.strip()
    if not raw:
        return None
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", raw) and not raw.startswith("//"):
        raw = "https://" + raw
    try:
        parts = urlsplit(raw)
    except ValueError:
        return None
    if not parts.netloc:
        return None
    host = parts.netloc.lower()
    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    kept = []
    for kv in parts.query.split("&") if parts.query else []:
        if not kv:
            continue
        key = kv.split("=", 1)[0].lower()
        if key in _NOISE_QUERY_PARAMS:
            continue
        kept.append(kv)
    query = "&".join(kept)
    defragged = urldefrag(urlunsplit((parts.scheme.lower() or "https", host, path, query, "")))
    return defragged.url


def url_domain(url: str | None) -> str | None:
    """Return the lowercased host of ``url`` (or ``None``)."""
    canon = canonicalize_url(url)
    if canon is None:
        return None
    try:
        return urlsplit(canon).netloc.lower()
    except ValueError:
        return None
