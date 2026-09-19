"""Telegram accepts 4096 characters per message, so long answers are split.

A part never ends inside an HTML entity or inside a tag, because Telegram would reject
the message: the text is cut at block boundaries (a person, then a line), and only a line
longer than a whole part is cut by characters — outside any tag.
"""

from __future__ import annotations

from collections.abc import Sequence

TELEGRAM_MESSAGE_LIMIT = 4096
# Headroom for the "часть N из M" suffix the caller may add.
DEFAULT_PART_LIMIT = 3900
# A run away answer is cut instead of flooding the chat with hundreds of messages.
DEFAULT_MAX_PARTS = 10
TRUNCATION_NOTICE = "\n\n…ответ слишком длинный, показана только часть. Уменьшите период или limit."


def split_blocks(
    blocks: Sequence[str],
    *,
    limit: int = DEFAULT_PART_LIMIT,
    max_parts: int = DEFAULT_MAX_PARTS,
    separator: str = "\n\n",
) -> list[str]:
    """Keep every block whole while it fits; never split a tag or an entity."""
    parts: list[str] = []
    current = ""
    for block in blocks:
        for piece in _fitting_pieces(block, limit):
            candidate = piece if not current else f"{current}{separator}{piece}"
            if len(candidate) <= limit:
                current = candidate
                continue
            if current:
                parts.append(current)
            current = piece
            if len(parts) == max_parts:
                return _truncated(parts, limit)
    if current:
        parts.append(current)
    return parts[:max_parts] if len(parts) <= max_parts else _truncated(parts[:max_parts], limit)


def _fitting_pieces(block: str, limit: int) -> list[str]:
    """A block too long for one message, cut at line boundaries, then at safe characters."""
    if len(block) <= limit:
        return [block]
    pieces: list[str] = []
    current = ""
    for line in block.split("\n"):
        candidate = line if not current else f"{current}\n{line}"
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            pieces.append(current)
        current = ""
        pieces.extend(_cut_text(line, limit))
        if pieces and len(pieces[-1]) < limit:
            current = pieces.pop()
    if current:
        pieces.append(current)
    return pieces


def _cut_text(text: str, limit: int) -> list[str]:
    """Cut a single long line so that no piece ends inside a tag or an entity."""
    pieces: list[str] = []
    rest = text
    while len(rest) > limit:
        cut = _safe_cut(rest, limit)
        pieces.append(rest[:cut])
        rest = rest[cut:]
    if rest:
        pieces.append(rest)
    return pieces


def _safe_cut(text: str, limit: int) -> int:
    """The last position within `limit` that is outside any `<…>` tag and any `&…;` entity."""
    cut = limit
    window = text[:limit]
    open_tag = window.rfind("<")
    if open_tag != -1 and window.find(">", open_tag) == -1:
        cut = open_tag
    entity = text.rfind("&", 0, cut)
    if entity != -1 and ";" not in text[entity:cut]:
        cut = entity
    # A space is a nicer break than the middle of a word, when one is close by.
    space = text.rfind(" ", max(0, cut - 120), cut)
    return space if space > 0 else max(cut, 1)


def _truncated(parts: list[str], limit: int) -> list[str]:
    last = parts[-1]
    room = limit - len(TRUNCATION_NOTICE)
    parts[-1] = (last if len(last) <= room else last[: _safe_cut(last, room)]) + TRUNCATION_NOTICE
    return parts
