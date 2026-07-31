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
from court_monitor.domain.models import PERSON_NAME_FIELD, VerificationStatus
from court_monitor.extraction._utils import quote_around
from court_monitor.observability import get_logger

if TYPE_CHECKING:
    from spacy.language import Language
    from spacy.tokens import Span

_MODEL_NAME = "ru_core_news_lg"
_INITIAL_RE = re.compile(r"^[А-ЯЁ]\.$")

_log = get_logger(__name__)


class NerModelUnavailable(RuntimeError):
    """``CM_NER_MODE=spacy`` is set but the model is not installed."""


def _import_spacy():
    """Indirection so the absence of the model can be exercised in tests.

    spacy is an optional (`nlp` extra) dependency, imported lazily.
    """
    import spacy  # noqa: PLC0415

    return spacy


@lru_cache(maxsize=1)
def _load_attempt() -> Language | Exception:
    """Load the model once, remembering failure as well as success.

    ``lru_cache`` alone was not enough: it does not memoize exceptions, so a
    missing model meant every document re-ran ``spacy.load`` and re-logged the
    same error. The retry itself is cheap (a failed load measures ~0.1 ms, so
    the whole corpus wastes about 0.05 s — not the reason to fix this); the
    cost is the log. One setup mistake produced 379 identical ERROR lines, and
    an ERROR that repeats per document is one nobody reads, which is where a
    genuine error goes to hide.

    *Returning* the outcome rather than raising is what makes it cacheable, so
    the log happens once as a consequence, with no flag to keep in sync.

    ``ImportError`` counts as the same kind of failure as ``OSError``: spacy
    itself is an optional extra, so "the model is missing" and "the extra was
    never installed" are one setup mistake, and neither should cost the
    document its other facts.
    """
    try:
        return _import_spacy().load(
            _MODEL_NAME, disable=["morphologizer", "parser", "attribute_ruler", "lemmatizer"]
        )
    except (OSError, ImportError) as exc:
        _log.error("ner.model_unavailable", model=_MODEL_NAME, error=str(exc))
        return exc


def reset_model_cache() -> None:
    """Forget the load outcome. For tests; nothing in the pipeline calls it."""
    _load_attempt.cache_clear()


def _get_nlp() -> Language:
    outcome = _load_attempt()
    if isinstance(outcome, Exception):
        raise NerModelUnavailable(
            f"NER недоступен: модель {_MODEL_NAME} не загружена. "
            f"Установите extra `nlp` и выполните: python -m spacy download {_MODEL_NAME}"
        ) from outcome
    return outcome


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
                field=PERSON_NAME_FIELD,
                value=full,
                verification_status=VerificationStatus.inferred,
                confidence=confidence,
                quote=quote_around(text, span.start_char, span.end_char, window=30),
                source_url=source_url,
                extraction_method=method,
            )
        )
    return out
