"""Name extraction — deliberately conservative heuristic.

A proper FIO normalizer (ё/е, cases, OCR errors, double surnames, aliases)
arrives in Etap 6 (``normalization``). Here we only surface *candidate* full
names with low confidence; they are never auto-attributed to a person.

We look for runs of 2–3 Capitalized Cyrillic tokens, excluding common
non-name tokens. Initials like ``А. Б. Иванов`` are also recognized.
"""

from __future__ import annotations

import re

from court_monitor.domain.facts import ExtractedFactDTO
from court_monitor.domain.models import VerificationStatus

# Capitalized Cyrillic word, optional initial letter + dot.
_TOK = r"[А-ЯЁ][а-яёA-ЯЁ\-']+"
_INITIAL = r"[А-ЯЁ]\."

_TOKEN = rf"(?:{_TOK}|{_INITIAL})"

# 3-token "Surname Name Patronymic" or 2-token "Surname Name".
_NAME_RE = re.compile(rf"\b({_TOKEN})\s+({_TOKEN})(?:\s+({_TOKEN}))?\b")

# Words that look like names but are usually not (titles / boilerplate).
_STOPWORDS = frozenset(
    {
        "Суд",
        "Судья",
        "Прокурор",
        "Следователь",
        "Адвокат",
        "Российской",
        "Федерации",
        "Уголовного",
        "Кодекса",
        "Дело",
        "Гражданин",
        "Республики",
        "Москва",
        "Москве",
        "России",
        "Верховный",
        "Следственного",
        "Комитета",
        "Департамента",
    }
)


def extract_name_candidates(text: str, *, source_url: str | None = None) -> list[ExtractedFactDTO]:
    if not text:
        return []
    out: list[ExtractedFactDTO] = []
    seen: set[str] = set()
    for m in _NAME_RE.finditer(text):
        tokens = [t for t in m.groups() if t]
        if len(tokens) < 2:
            continue
        if any(_is_stopword(t) for t in tokens):
            continue
        full = " ".join(tokens)
        key = full.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(
            ExtractedFactDTO(
                entity="person",
                field="full_name_original",
                value=full,
                verification_status=VerificationStatus.unverified,
                confidence=0.4,
                quote=_quote_around(text, m.start(), m.end()),
                source_url=source_url,
                extraction_method="regex:name:heuristic",
            )
        )
    return out


def _is_stopword(token: str) -> bool:
    base = token.rstrip(".").split("-")[0]
    return base in _STOPWORDS


def _quote_around(text: str, start: int, end: int, *, window: int = 30) -> str:
    lo = max(0, start - window)
    hi = min(len(text), end + window)
    return re.sub(r"\s+", " ", text[lo:hi]).strip()
