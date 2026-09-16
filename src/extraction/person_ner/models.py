"""What a person-name recognizer returns, and the contract every one of them keeps.

Detection only: a recognizer answers «is this span a person's name», never «is this the
same person as that one». Identity stays with entity resolution.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, order=True)
class PersonNameSpan:
    """One person-name mention, in offsets of the text it was recognized in.

    `surface_text` is the text as written («Андрея Кузнецова»), never a normalized form:
    normalization is a later layer and must not lose what the article actually said.
    """

    start_offset: int
    end_offset: int
    surface_text: str
    confidence: float

    def __post_init__(self) -> None:
        if self.start_offset < 0 or self.end_offset <= self.start_offset:
            raise ValueError(f"empty or negative span: [{self.start_offset}, {self.end_offset})")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence out of range: {self.confidence}")


class PersonNameRecognizer(Protocol):
    """Finds person-name mentions in a text.

    Every returned span satisfies `text[span.start_offset : span.end_offset] ==
    span.surface_text`, and the result is sorted by `(start_offset, end_offset)`.
    """

    def recognize(self, text: str) -> list[PersonNameSpan]: ...
