"""A conservative eligibility gate before the relevance classifier.

It rules out, before any model reads them, only the articles that
`court-monitor-person-criminal-case-v2` makes false whatever else they say: a digest of
several news items, and — when the text shows no criminal case at all — an
administrative-only, an election-only or an organization-only article. In doubt it lets
the article through: a criminal marker anywhere in the text (`CRIMINAL`) keeps every
non-digest article for the classifier.

It decides nothing about ordinary crime, treason, terrorism, political cases or arson:
those are the classifier's.
"""

from __future__ import annotations

import re

DIGEST = "digest"
ADMINISTRATIVE = "administrative"
ELECTION = "election"
ORGANIZATION = "organization_only"

# A digest names itself at its head: the title or the first words of the text.
_DIGEST = re.compile(
    r"дайджест|главные новости|главное за (сегодняшний )?день|что случилось\?|"
    r"судный день|сумеречная медиазона|чем запомнилась неделя|"
    r"эксклюзивные материалы свежего номера",
    re.IGNORECASE,
)
DIGEST_HEAD = 160

# Any sign of a criminal case: then the gate lets the article through.
CRIMINAL = re.compile(
    r"уголовн|\bУК\b|приговор|обвиня|обвинени|подозрева|СИЗО|под страж|"
    r"мер\w* пресечени|лишени\w* свободы|колони[ия]|осуди|осужд|"
    r"заочно арест|в розыск|по делу о|дело о|дела о|теракт|госизмен|"
    # A verdict: «признан виновным», «признана виновной», «признали виновными» …
    r"признал?\w*(?:\s+[^\s.]+){0,2}?\s+виновн|"
    # A sentence or a charge: «условный срок», «реальный срок», «вменили» …
    r"условн\w* (срок|наказани)|реальн\w* срок|\bвмен(ил|ила|или|яет|яют|яется|яются)\b",
    re.IGNORECASE,
)
_ADMINISTRATIVE = re.compile(
    r"КоАП|административн\w* (арест|штраф|протокол|правонарушени|ответственност|дел)",
    re.IGNORECASE,
)
_ELECTION = re.compile(
    r"кандидат|избирком|избирательн\w* комисси|\bЦИК\b|\bТИК\b|\bУИК\b|"
    r"партийн\w* список|список парти|списк\w* «|с выборов|агитаци|выборах? в",
    re.IGNORECASE,
)
# Organization-only needs, in one sentence, an organization named as such and an action
# that only an organization undergoes; «признали виновной … к экстремистской
# деятельности» names no organization and an action against a person.
_ORGANIZATION_ANCHOR = re.compile(
    r"организаци|движени|\bфонд|объединени|\bпарти(я|и|ю|ей)\b|юрлиц|юридическ\w* лиц|"
    r"\bобществ(о|а|у|ом|е)\b|\bНКО\b|\bНПО\b",
    re.IGNORECASE,
)
_ORGANIZATION_ACTION = re.compile(
    r"(признал\w*|признан\w*|внес\w*|включ\w*)\b(?:(?!виновн)[^.]){0,80}?"
    r"(экстремистск|террористическ|нежелательн)\w*|"
    r"ликвидир\w*|ликвидаци\w*|запрет\w*[^.]{0,30}деятельност|приостанови\w*[^.]{0,30}деятельност|"
    r"\bиск\w*[^.]{0,40}(о признании|о ликвидации|о запрете)|конфиск\w*",
    re.IGNORECASE,
)
# A person's tie to an organization: then the article is about the person, never
# organization-only — «переводил деньги», «пожертвовал», «финансировал», «участвовал»,
# «состоял», «в деятельности организации».
_PERSON_TIE = re.compile(
    r"\bперев(од|одил|ёл|ел|ела|ели)\w*|\bперечисл\w*|пожертвова\w*|\bдонат\w*|финансирова\w*|\bучасти[еяю]\b|"
    r"участвова\w*|\bсостоя\w* в\b|\bчлен(ом|ами|ство\w*)\b|деятельност\w* (запрещ\w* )?организаци",
    re.IGNORECASE,
)
_SENTENCE = re.compile(r"(?<=[.!?…])\s+")


def is_organization_only(text: str) -> bool:
    """An organization named as such and an organization-only action in one sentence, and
    nowhere in the text a person's tie to an organization."""
    if _PERSON_TIE.search(text):
        return False
    return any(
        _ORGANIZATION_ACTION.search(sentence) and _ORGANIZATION_ANCHOR.search(sentence)
        for sentence in _SENTENCE.split(text)
    )


def is_digest(title: str, text: str) -> bool:
    return bool(_DIGEST.search(title) or _DIGEST.search(text[:DIGEST_HEAD]))


def gate(title: str, text: str) -> str | None:
    """The category that rules the article out, or None to let the classifier decide."""
    body = f"{title} {text}"
    if is_digest(title, text):
        return DIGEST
    if CRIMINAL.search(body):
        return None
    if _ADMINISTRATIVE.search(body):
        return ADMINISTRATIVE
    if is_organization_only(body):
        return ORGANIZATION
    if _ELECTION.search(body):
        return ELECTION
    return None
