"""The people the customer's channel has not written about yet, with a post to start from.

The channel publishes people whatever their Rosfinmonitoring status (half of its people
since August 1 are on the list), so the queue shows the status instead of filtering by it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from candidates.models import PoliticalPersecutionCandidate, RosfinmonitoringStatus
from channel_feed.published import name_key

_ARTICLE_REASON = "Политическая статья:"
_EVENT_WORDS = {
    "case_opened": "возбуждено уголовное дело",
    "charge": "предъявлено обвинение",
    "arrest": "арестован(а)",
    "sentence": "вынесен приговор",
    "detention": "задержан(а)",
    "search": "прошёл обыск",
}
_RF_NOTES = {
    RosfinmonitoringStatus.MATCHED: "в перечне Росфинмониторинга",
    RosfinmonitoringStatus.NEEDS_REVIEW: "возможно, в перечне Росфинмониторинга",
    RosfinmonitoringStatus.AMBIGUOUS: "возможно, в перечне Росфинмониторинга",
}


@dataclass(frozen=True)
class QueueSource:
    url: str
    event_type: str | None


def is_published(candidate: PoliticalPersecutionCandidate, published: Iterable[str]) -> bool:
    keys = set(published)
    return any(
        (key := name_key(name)) is not None and key in keys
        for name in (candidate.canonical_name, candidate.normalized_name)
    )


def articles_of(reasons: Iterable[str]) -> list[str]:
    """«УК РФ ст. 205.2 ч. 2» → «ч. 2 ст. 205.2 УК РФ», each article once, in order."""
    found: list[str] = []
    for reason in reasons:
        if not reason.startswith(_ARTICLE_REASON):
            continue
        for item in reason.removeprefix(_ARTICLE_REASON).split(";"):
            match = re.fullmatch(
                r"\s*(УК РФ|КоАП РФ) ст\. ([\d.]+)(?: ч\. ([\d.]+))?(?: п\. (\S+))?\s*", item
            )
            if match is None:
                continue
            code, article, part, clause = match.groups()
            text = " ".join(
                piece
                for piece in (
                    f"п. «{clause}»" if clause else "",
                    f"ч. {part}" if part else "",
                    f"ст. {article} {code}",
                )
                if piece
            )
            if text not in found:
                found.append(text)
    return found


def draft_post(
    candidate: PoliticalPersecutionCandidate,
    source: QueueSource | None,
    name: str | None = None,
) -> str:
    """A starting point for the post: who, what happened, on which articles, where from.

    `name`: how to write the person, surname first as the customer's table does.
    """
    lines = [name or candidate.canonical_name]
    happened = _EVENT_WORDS.get(source.event_type or "") if source is not None else None
    articles = articles_of(candidate.persecution_reasons)
    facts = ", ".join(
        part
        for part in (
            happened.capitalize() if happened else "",
            f"статьи: {', '.join(articles)}" if articles else "",
        )
        if part
    )
    if facts:
        lines.append(facts + ".")
    note = _RF_NOTES.get(candidate.rosfinmonitoring_status)
    if note:
        lines.append(f"Внимание: {note}.")
    if source is not None:
        lines.append(f"Источник: {source.url}")
    return "\n".join(lines)
