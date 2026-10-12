"""Words of a Russian name, read by rules."""

from __future__ import annotations

import re

_PATRONYMIC = re.compile(
    r"^([а-я]+?)(ович|евич|ьич|ич|овна|евна|ична|инична)(а|у|ем|е|ы|ой|ою|и)?$"
)


def patronymic_key(word: str) -> str | None:
    """«Петровича», «Петровна» → «петр»: one father's name whatever the sex and case."""
    match = _PATRONYMIC.match(word.lower().replace("ё", "е"))
    if match is None or len(match.group(1)) < 3:
        return None
    return match.group(1)
