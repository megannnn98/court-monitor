"""Turning token predictions into person spans, without any model in the way.

Every rule here is decided on offsets and labels alone, so the risky parts — window
boundaries, subword merging and deduplication — are testable without downloading a model.
"""

from __future__ import annotations

from dataclasses import dataclass

from extraction.person_ner.models import PersonNameSpan

# A person-name label, whatever prefix scheme the model uses: `B-PER`, `I-PER`, `PER`,
# `B-PERSON`. Anything else (`O`, `B-ORG`, …) is not a person.
_PERSON_SUFFIXES = ("PER", "PERSON")


@dataclass(frozen=True)
class TokenPrediction:
    """One token's label, scored, in offsets of the text it was predicted on."""

    label: str
    score: float
    start: int
    end: int


def is_person_label(label: str) -> bool:
    return label.rsplit("-", 1)[-1].upper() in _PERSON_SUFFIXES


def begins_entity(label: str) -> bool:
    """`B-` starts a new entity; a bare `PER` or an `I-` continues the run before it."""
    return label.upper().startswith("B-")


def window_bounds(length: int, *, window: int, stride: int) -> list[tuple[int, int]]:
    """Character windows covering [0, length) with `stride` characters of overlap.

    The overlap is what keeps a name written across a boundary from being lost: it is
    whole in whichever window does not cut it.
    """
    if window <= 0:
        raise ValueError("window must be greater than zero")
    if not 0 <= stride < window:
        raise ValueError("stride must be at least zero and smaller than the window")
    if length <= window:
        return [(0, length)]

    bounds: list[tuple[int, int]] = []
    start = 0
    step = window - stride
    while start < length:
        end = min(start + window, length)
        bounds.append((start, end))
        if end == length:
            break
        start += step
    return bounds


def merge_person_tokens(
    text: str,
    predictions: list[TokenPrediction],
    *,
    min_score: float,
    offset: int = 0,
) -> list[PersonNameSpan]:
    """Consecutive person tokens become one span, scored by its weakest token.

    `offset` shifts window-local offsets back onto the article, so a span always carries
    absolute offsets. The span text is read from `text`, never rebuilt from the tokens:
    subword pieces («Кузне», «цова») must not reappear glued or spaced differently.
    """
    spans: list[PersonNameSpan] = []
    run_start: int | None = None
    run_end = 0
    run_score = 1.0

    def flush() -> None:
        nonlocal run_start
        if run_start is None:
            return
        surface = text[run_start:run_end].strip()
        if surface and run_score >= min_score:
            start = run_start + text[run_start:run_end].index(surface)
            spans.append(
                PersonNameSpan(
                    start_offset=offset + start,
                    end_offset=offset + start + len(surface),
                    surface_text=surface,
                    confidence=round(run_score, 4),
                )
            )
        run_start = None

    for prediction in sorted(predictions, key=lambda item: (item.start, item.end)):
        if not is_person_label(prediction.label):
            flush()
            continue
        starts_new = begins_entity(prediction.label) or run_start is None
        # A gap of anything but whitespace ends the run: «Иванов, Петров» is two people.
        if run_start is not None and text[run_end : prediction.start].strip():
            starts_new = True
        if starts_new:
            flush()
            run_start, run_end, run_score = prediction.start, prediction.end, prediction.score
            continue
        run_end = prediction.end
        run_score = min(run_score, prediction.score)
    flush()
    return spans


def deduplicate(spans: list[PersonNameSpan]) -> list[PersonNameSpan]:
    """One span per place: overlapping windows see the same name twice.

    The longest span wins, and the highest confidence breaks a tie, so a name that one
    window cut short does not replace the whole one another window saw.
    """
    ordered = sorted(
        spans,
        key=lambda span: (
            span.start_offset,
            -(span.end_offset - span.start_offset),
            -span.confidence,
        ),
    )
    kept: list[PersonNameSpan] = []
    for span in ordered:
        if any(
            span.start_offset < other.end_offset and other.start_offset < span.end_offset
            for other in kept
        ):
            continue
        kept.append(span)
    return sorted(kept, key=lambda span: (span.start_offset, span.end_offset))
