"""A person recognizer for tests: no model, no download, no GPU."""

from __future__ import annotations

from extraction.person_ner.models import PersonNameSpan


class FakePersonNameRecognizer:
    """Recognizes the names it was told about, wherever they appear in the text.

    Keeps the protocol's contract: spans carry the surface form as written, offsets point
    into the text given, and the result is sorted by offsets.
    """

    def __init__(self, names: list[str], *, confidence: float = 0.9) -> None:
        self._names = names
        self._confidence = confidence

    def recognize(self, text: str) -> list[PersonNameSpan]:
        spans: list[PersonNameSpan] = []
        for name in self._names:
            start = text.find(name)
            while start != -1:
                spans.append(
                    PersonNameSpan(
                        start_offset=start,
                        end_offset=start + len(name),
                        surface_text=name,
                        confidence=self._confidence,
                    )
                )
                start = text.find(name, start + len(name))
        return sorted(spans, key=lambda span: (span.start_offset, span.end_offset))
