"""Text preprocessing under test: service noise removed, words lemmatized, or both.

`cleanup` removes only what a channel adds to every post — links, subscription calls,
mirror notices, mandated labels — found in the corpus itself. Each pattern is anchored on
its exact wording, so a sentence of the article never goes with it; a call glued to the end
of a sentence takes the call alone.

`lemmatize` puts each Cyrillic word in its dictionary form (pymorphy3, first parse) and
lowercases every other token as it is: Latin, numbers and the rest stay as they were, so
the experiment is about lemmas and nothing else. No stop words, no stemming.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from functools import cache

from pymorphy3 import MorphAnalyzer

# A sentence's own ending stays with the sentence: what precedes a call is either the start
# of a line, spaces, or the end of a sentence.
_BEFORE = r"(?:^|[^\S\n]+|(?<=[.!?…»\"]))"
# An emoji or a bullet of a call: up to three marks, none of them a sentence's end.
_MARK = r"(?:[^\w\s.!?…«»\"]{1,3}[^\S\n]*)?"

_PATTERNS = (
    # A link: to a site, a Telegram, MAX or VK channel, or a Telegram handle.
    re.compile(r"(?i)\b(?:https?://|www\.)\S+"),
    re.compile(r"(?i)\b(?:t\.me|max\.ru|vk\.com)/\S*"),
    re.compile(r"(?<![\w.])@\w{3,}"),
    # A label the law requires of a foreign agent's material (upper case in the corpus).
    re.compile(
        r"НАСТОЯЩИЙ МАТЕРИАЛ \(ИНФОРМАЦИЯ\) ПРОИЗВЕДЕН[^\n]{0,300}?"
        r"ДЕЯТЕЛЬНОСТИ ИНОСТРАННОГО АГЕНТА (?:ООО [\"“][^\"”\n]{1,40}[\"”]|[^\n]{0,60}?«[^»\n]{1,40}»)"
        r"\.?(?:[^\S\n]*18\+)?"
    ),
    # The disclaimer a channel of an organization called extremist puts under every post.
    re.compile(
        r"Этот пост написал AI\. А опубликовала, по мнению российских властей, "
        r"экстремистская организация\. Публично делиться им может быть небезопасно\."
    ),
    # «Медиазона»'s appeal for donations: a line of its own ending in the donation link.
    re.compile(r"(?m)^[^\n]{0,250}donate\.zona\.media/?[^\S\n]*$"),
    # Subscription calls.
    re.compile(rf"(?im){_BEFORE}{_MARK}Здесь мы всегда на связи\. Подписывайтесь\b[^\n]{{0,100}}$"),
    re.compile(
        rf"(?im){_BEFORE}{_MARK}Подписаться \| (?:Помочь RusNews|Написать нам \| Задонатить)"
    ),
    re.compile(r"(?im)^[^\w\n]*Подписаться на\b[^\n]{0,120}$"),
    re.compile(
        rf"(?im){_BEFORE}{_MARK}(?:подписывайтесь|подписывайся|подпишитесь)"
        r"(?:[^\S\n]+на\b[^\n]{0,100}|[.…:!]?)[^\S\n]*$"
    ),
    # «Photos do not load? Read us in MAX» and its kin.
    re.compile(rf"(?im){_BEFORE}{_MARK}(?:если у вас )?не грузятся фото\b[^\n]{{0,100}}$"),
    re.compile(rf"(?im){_BEFORE}{_MARK}читайте нас в\b[^\n]{{0,80}}$"),
    re.compile(r"(?im)^[^\w\n]*(?:мы в (?:макс|max)|наши каналы:[^\n]{0,80})[^\w\n]*$"),
    # A channel's own name with its mirror: «BAZA в MAX», «SHOT MAX:», «ОС в Telegram».
    re.compile(r"\b(?:SPORT )?(?:BAZA|SHOT|ОС)(?: в)? (?:MAX|Telegram)\b:?"),
)


def cleanup(text: str) -> str:
    """`text` without the service noise of its channel; everything else as it was."""
    for pattern in _PATTERNS:
        text = pattern.sub("", text)
    return text


_TOKEN = re.compile(r"\w+")
_CYRILLIC = re.compile(r"[а-яё]+")


@cache
def _analyzer() -> MorphAnalyzer:
    return MorphAnalyzer()


@cache
def _lemma(word: str) -> str:
    if not _CYRILLIC.fullmatch(word):
        return word
    return str(_analyzer().parse(word)[0].normal_form)


def lemmatize(text: str) -> str:
    """The words of `text`, lowercased, Cyrillic ones in their dictionary form, one space
    apart. Punctuation goes, as it goes in TF-IDF's own tokens."""
    return " ".join(_lemma(token.lower()) for token in _TOKEN.findall(text))


def raw(text: str) -> str:
    return text


def cleanup_lemma(text: str) -> str:
    return lemmatize(cleanup(text))


# The variants compared, in order of simplicity: a tie goes to the earlier.
PREPROCESSORS: dict[str, Callable[[str], str]] = {
    "raw": raw,
    "cleanup": cleanup,
    "lemma": lemmatize,
    "cleanup_lemma": cleanup_lemma,
}
