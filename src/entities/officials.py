"""Officials among the entities: judges, prosecutors, investigators, governors, ministers.

An official is named in a case, never its figurant: «глава СК Александр Бастрыкин» in a
sentence about a man who insulted him is not charged with anything. Four sources, the
first that speaks wins:

1. a person's mark (`EntityOfficialMarkRecord`), either way;
2. the exclusion list (`ExcludedPersonRecord`), synced from Airtable: the people a
   person wrote down as never being a target;
3. the texts: a title right before the name («судья Ольга Минакова», «главе СК
   Александру Бастрыкину») in at least two mentions or half of them — once among many
   is the title of someone beside («критик главы региона Ростислав Мурзагулов»);
   «бывший судья» is no title;
4. the model's role of step 4 (judge, prosecutor, police, official).

Deputies are no officials here: opposition deputies are among the persecuted.
"""

from __future__ import annotations

import itertools
import logging
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from db.orm_models import (
    EntityGroupPoliticsRecord,
    EntityGroupRecord,
    EntityGroupRoleRecord,
    EntityOfficialMarkRecord,
    ExcludedPersonRecord,
)
from entities.disputes import KeyIndex
from entities.grouping import _fold

logger = logging.getLogger("entities")

# The model's roles that are officials.
OFFICIAL_KINDS = frozenset({"judge", "prosecutor", "police", "official"})
# Characters before a mention searched for a title.
TITLE_CONTEXT = 80
# The words that may stand between a title and the name: the court or body it heads.
_BODY = (
    r"(?:(?:[а-я]+ского|[а-я]+ской|[а-я]+ного|суда|судов|ск|рф|россии|мвд|фсб|гу|управления|"
    r"следственного|комитета|москвы|петербурга|области|края|республики|района|региона|по|"
    r"обороны|юстиции|внутренних|иностранных|дел|финансов|здравоохранения|образования|"
    r"культуры|экономики|просвещения|транспорта)\s+)"
    r"{0,5}"
)
_TITLE = re.compile(
    r"(?<![а-я-])(бывш[а-я]*\s+|экс-)?"
    r"(судь[яиейю]|генпрокурор[а-я]*|прокурор[а-я]*|следовател[а-я]*"
    r"|председател[а-я]*\s+(?:ск|следственного|суда)"
    r"|глав[аыеу]\s+(?:ск|следственного|мвд|фсб|росгвардии|региона|республики|области|края|города)"
    r"|губернатор[а-я]*|министр[а-я]*|мэр[а-я]*"
    r"|начальник[а-я]*\s+(?:полиции|мвд|управления|уфсб|гу|отдела|следственного))"
    r"\s+" + _BODY + r"$"
)
_KIND_OF_TITLE = (
    ("судь", "judge"),
    ("генпрокурор", "prosecutor"),
    ("прокурор", "prosecutor"),
    ("следовател", "police"),
    ("начальник", "police"),
    ("председател", "police"),
)
_POLICE_BODIES = ("ск", "следственного", "мвд", "фсб", "росгвардии")

_CONTEXTS = text(
    """
    SELECT gm.group_id,
           substring(a.text from greatest(m.start_offset - :context + 1, 1)
                     for least(m.start_offset, :context))
    FROM entity_group_mentions gm
    JOIN entity_mentions m ON m.id = gm.mention_id
    JOIN article_extraction_runs r ON r.id = m.extraction_run_id
    JOIN parsed_articles a ON a.id = r.article_id
    WHERE gm.group_id = ANY(:groups)
    """
)


def title_before(context: str) -> str | None:
    """The title right before a mention, folded; None for none or a former one."""
    found = _TITLE.search(" ".join(context.lower().replace("ё", "е").split()) + " ")
    if found is None or found.group(1):
        return None
    return found.group(2)


def kind_of_title(title: str) -> str:
    """«судья» → judge, «главе СК» → police, «губернатор» → official."""
    for stem, kind in _KIND_OF_TITLE:
        if title.startswith(stem):
            if stem == "председател" and title.endswith("суда"):
                return "judge"
            return kind
    if title.startswith("глав") and title.split()[-1] in _POLICE_BODIES:
        return "police"
    return "official"


def titled(contexts: Iterable[tuple[int, str]]) -> dict[int, tuple[str, str]]:
    """Per entity with a title in at least two mentions or half of them: (kind, title)."""
    mentions: Counter[int] = Counter()
    titles: dict[int, Counter[str]] = {}
    for group_id, context in contexts:
        mentions[group_id] += 1
        title = title_before(context or "")
        if title is not None:
            titles.setdefault(group_id, Counter())[title] += 1
    found: dict[int, tuple[str, str]] = {}
    for group_id, counted in titles.items():
        total = sum(counted.values())
        if total >= 2 or total * 2 >= mentions[group_id]:
            title = counted.most_common(1)[0][0]
            found[group_id] = (kind_of_title(title), title)
    return found


def titled_entities(session: Session, group_ids: Sequence[int]) -> dict[int, tuple[str, str]]:
    rows = session.execute(_CONTEXTS, {"groups": list(group_ids), "context": TITLE_CONTEXT})
    return titled((int(group_id), str(context or "")) for group_id, context in rows)


def _key_candidates(name: str) -> list[str]:
    """Every spelling of `name` that could be an entity key, folded.

    An entity key reads the given name first («иван иванов»), while a list a person
    keeps by hand usually writes the surname first and adds what the articles did not
    («Иванов Иван Иванович»). Rather than guess which word is which, or which of them
    the entity carries, every order of every subset of two or three words is offered to
    the key index, which knows today's keys and the stems of yesterday's.

    Single words are deliberately not offered: a bare surname names half the city, and
    excluding whichever entity happens to carry it would be a guess. A list entry that
    means a bare surname is written for the person the articles name in full.
    """
    words = _fold(name).split()[:3]
    candidates: list[str] = []
    for count in range(len(words), 1, -1):
        for combination in itertools.permutations(words, count):
            candidate = " ".join(combination)
            if candidate not in candidates:
                candidates.append(candidate)
    return candidates


def official_entity_ids(
    session: Session, keys: Mapping[int, str]
) -> dict[int, ExcludedPersonRecord]:
    """The active officials the list names, for the exactly one entity each matches, per entity id.

    A row that names no entity of this database is left out: it may be a person the
    articles have not mentioned yet, which is nothing to act on. A row that names
    several — two people whose keys the same words could stand for — is left out too:
    excluding the wrong one of two is worse than excluding neither.
    """
    active = list(session.scalars(select(ExcludedPersonRecord).where(ExcludedPersonRecord.active)))
    index = KeyIndex(keys.values())
    by_key = {key: group_id for group_id, key in keys.items()}
    found: dict[int, ExcludedPersonRecord] = {}
    for record in active:
        resolved = {
            by_key[today]
            for today in (index.today(candidate) for candidate in _key_candidates(record.full_name))
            if today is not None
        }
        if len(resolved) > 1:
            logger.warning(
                "event=official_list_ambiguous person=%s entities=%d",
                record.full_name,
                len(resolved),
            )
            continue
        if resolved:
            found[resolved.pop()] = record
    return found


def official_marks(session: Session, keys: Mapping[int, str]) -> dict[int, bool]:
    """A person's marks per entity id; a mark on an older key holds for the entity it
    meant (`KeyIndex`)."""
    index = KeyIndex(keys.values())
    by_key = {key: group_id for group_id, key in keys.items()}
    marks: dict[int, bool] = {}
    for record in session.scalars(select(EntityOfficialMarkRecord)):
        today = index.today(record.key)
        if today is not None:
            marks[by_key[today]] = record.official
    return marks


def mark_official(session: Session, entity: EntityGroupRecord, official: bool) -> None:
    """A person's mark, in the caller's transaction, applied at once: an official leaves
    the figurants and «Результат» now, not at the next step 4."""
    record = session.get(EntityOfficialMarkRecord, entity.key)
    if record is None:
        session.add(EntityOfficialMarkRecord(key=entity.key, official=official))
    else:
        record.official = official
    role = session.get(EntityGroupRoleRecord, entity.id)
    if role is None:
        role = EntityGroupRoleRecord(group_id=entity.id, role="", method="", reason="", quote="")
        session.add(role)
    if official:
        kind = role.kind if role.kind in OFFICIAL_KINDS else "official"
        role.role, role.kind, role.method = "mentioned", kind, "official"
        role.reason = "должностное лицо — отмечен вручную"
        session.execute(
            delete(EntityGroupPoliticsRecord).where(EntityGroupPoliticsRecord.group_id == entity.id)
        )
    else:
        role.role, role.kind, role.method = "unclear", None, "manual"
        role.reason = "пометка должностного лица снята вручную; роль определит шаг 5"
