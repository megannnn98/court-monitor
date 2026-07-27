"""Name extraction — deliberately conservative heuristic.

A proper FIO normalizer (ё/е, cases, OCR errors, double surnames, aliases)
arrives in Etap 6 (``normalization``). Here we only surface *candidate* full
names with confidence based on token count and pattern quality.

We look for runs of 2–3 Capitalized Cyrillic tokens, excluding common
non-name tokens (courts, organizations, positions). Initials like
``А. Б. Иванов`` are also recognized but with lower confidence.
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
_NAME_RE = re.compile(rf"\b({_TOKEN})\s+({_TOKEN})(?:\s+({_TOKEN}))?(?=\s|$|[.,;:!?])")

# Words that look like names but are usually not (titles / boilerplate /
# courts / organizations / positions).
_STOPWORDS = frozenset(
    {
        # Court / legal
        "Суд",
        "Судья",
        "Прокурор",
        "Следователь",
        "Адвокат",
        "Подсудимый",
        "Истец",
        "Ответчик",
        "Судебный",
        "Судебная",
        "Судебное",
        "Судебные",
        # Organizations
        "Российской",
        "Федерации",
        "Уголовного",
        "Кодекса",
        "УК",
        "РФ",
        "Дело",
        "Гражданин",
        "Республики",
        "Верховный",
        "Следственного",
        "Комитета",
        "Департамента",
        "Министерства",
        "Прокуратуры",
        "Прокуратура",
        "Управления",
        "Управление",
        "Окружной",
        "Районный",
        "Городской",
        "Мировой",
        "Арбитражный",
        # Positions
        "Председатель",
        "Заместитель",
        "Начальник",
        "Директор",
        "Руководитель",
        # Geography (common in court names)
        "Москва",
        "Москве",
        "Московский",
        "Россия",
        "России",
        # Legal terms
        "Уголовном",
        "Гражданском",
        "Административном",
        "Процессуальный",
        # Other boilerplate
        "Действия",
        "Действий",
        "Обстоятельства",
        "Установил",
        "Установила",
        "Установлено",
        "Рассмотрев",
        "Вынес",
        "Вынесла",
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
        # Skip if all tokens are initials (low quality)
        all_initials = all(re.match(r"[А-ЯЁ]\.", t) for t in tokens)
        full = " ".join(tokens)
        key = full.lower()
        if key in seen:
            continue
        seen.add(key)

        # Confidence: full FIO > partial > initials-only
        if len(tokens) == 3 and not all_initials:
            confidence = 0.95
            method = "regex:name:full_fio"
        elif len(tokens) == 3 and all_initials:
            confidence = 0.60
            method = "regex:name:initials_with_surname"
        elif len(tokens) == 2 and any(re.match(r"[А-ЯЁ]\.", t) for t in tokens):
            confidence = 0.50
            method = "regex:name:surname_initial"
        else:
            confidence = 0.70
            method = "regex:name:two_tokens"

        out.append(
            ExtractedFactDTO(
                entity="person",
                field="full_name_original",
                value=full,
                verification_status=VerificationStatus.inferred,
                confidence=confidence,
                quote=_quote_around(text, m.start(), m.end(), window=30),
                source_url=source_url,
                extraction_method=method,
            )
        )
    return out


def _is_stopword(token: str) -> bool:
    base = token.rstrip(".").split("-")[0]
    return base in _STOPWORDS


from court_monitor.extraction._utils import quote_around as _quote_around  # noqa: E402
