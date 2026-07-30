"""Name extraction via spaCy NER — complements the regex heuristic in ``names.py``.

The regex approach in ``names.py`` matches on capitalization patterns and is
blind to context, so it misses names that don't fit the "2-3 Title-Case
tokens" shape (e.g. a name split across an unusual line break, or a rare
pattern the stopword list doesn't cover). ``ru_core_news_lg``'s PER entities
are recognized from surrounding grammatical context (case endings, verb
agreement), catching some of what regex misses — at the cost of occasional
false positives on ambiguous capitalized phrases, which the caller must
account for as with any inferred fact.

The model is loaded once per process (``_get_nlp`` is memoized) since
loading ``ru_core_news_lg`` takes on the order of a second.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import TYPE_CHECKING

from court_monitor.domain.facts import ExtractedFactDTO
from court_monitor.domain.models import VerificationStatus
from court_monitor.extraction._utils import quote_around

if TYPE_CHECKING:
    from spacy.language import Language
    from spacy.tokens import Span

_MODEL_NAME = "ru_core_news_lg"
_INITIAL_RE = re.compile(r"^[А-ЯЁ]\.$")


@lru_cache(maxsize=1)
def _get_nlp() -> Language:
    import spacy  # noqa: PLC0415 — spacy is an optional (`nlp` extra) dependency

    return spacy.load(
        _MODEL_NAME, disable=["morphologizer", "parser", "attribute_ruler", "lemmatizer"]
    )


def _merge_adjacent_per_spans(ents: list[Span]) -> list[list[Span]]:
    """Merge consecutive PER entities with no tokens in between.

    The model frequently splits a genitive-case FIO — as in "в отношении
    Иванова Петра Сергеевича возбуждено..." — into two adjacent PER spans
    ("Иванова" / "Петра Сергеевича"). That construction is common in the
    court-report domain this extractor targets, so merge such runs back into
    one candidate rather than losing the patronymic or double-counting.
    """
    merged: list[list[Span]] = []
    run: list[Span] = []
    for ent in ents:
        if run and ent.label_ == "PER" and ent.start == run[-1].end:
            run.append(ent)
            continue
        if run:
            merged.append(run)
        run = [ent] if ent.label_ == "PER" else []
    if run:
        merged.append(run)
    return merged


def extract_name_candidates_ner(
    text: str, *, source_url: str | None = None
) -> list[ExtractedFactDTO]:
    if not text:
        return []
    doc = _get_nlp()(text)
    out: list[ExtractedFactDTO] = []
    seen: set[str] = set()
    for run in _merge_adjacent_per_spans(list(doc.ents)):
        span = doc[run[0].start : run[-1].end]
        full = re.sub(r"\s+", " ", span.text).strip()
        tokens = full.split(" ")
        if len(tokens) < 2:
            continue  # bare single token (given name or surname alone) is too ambiguous
        key = full.lower()
        if key in seen:
            continue
        seen.add(key)

        all_initials = all(_INITIAL_RE.match(t) for t in tokens)
        if len(tokens) >= 3 and not all_initials:
            confidence = 0.90
            method = "spacy:ner:per_full"
        elif all_initials or any(_INITIAL_RE.match(t) for t in tokens):
            confidence = 0.65
            method = "spacy:ner:per_initials"
        else:
            confidence = 0.75
            method = "spacy:ner:per_partial"

        out.append(
            ExtractedFactDTO(
                entity="person",
                field="full_name_original",
                value=full,
                verification_status=VerificationStatus.inferred,
                confidence=confidence,
                quote=quote_around(text, span.start_char, span.end_char, window=30),
                source_url=source_url,
                extraction_method=method,
            )
        )
    return out
