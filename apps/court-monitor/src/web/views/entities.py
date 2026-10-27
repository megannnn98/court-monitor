"""Person entities gathered from the publications with a criminal case: a list and a card.

A «статья» on these pages is a Criminal Code article; a news item is a «публикация»."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from analysis import read as analysis_read
from analysis.politics.classification import CRIMINAL, POLITICAL
from analysis.rosfinmonitoring.rf_check import FULL
from processing import read as identity
from processing.extraction.name_frequency import lookup_gender
from processing.identity.service import MANUAL
from processing.roles.roles import FIGURANT, KIND_LABELS, MENTIONED, POSSIBLE, UNCLEAR
from web.candidate_rows import surname_first

PAGE_SIZE = 100

RELATED_LIMIT = 30

QUOTE_CONTEXT = 160

EVENT_LABELS = {
    "case_opened": "Возбуждение дела",
    "charge": "Обвинение",
    "arrest": "Арест",
    "detention": "Задержание",
    "search": "Обыск",
    "sentence": "Приговор",
    "fine": "Штраф",
    "release": "Освобождение",
}


def display_name(name: str) -> str:
    """The name surname first, as the candidates read: «Иванов Иван Петрович».

    An initial stays an initial: «Соломатин П.» is already surname first; «Дмитрий С.»
    (a known given name and a surname's initial) reads «С. Дмитрий»."""
    words = name.split()
    initials = [word for word in words if "." in word]
    if not initials:
        return surname_first(name)
    full = [word for word in words if "." not in word]
    if len(full) == 1:
        if lookup_gender(full[0]) is not None:
            return " ".join([*initials, full[0]])
        return " ".join([full[0], *initials])
    return " ".join([surname_first(" ".join(full)), *initials])


def _surname_key(entity: identity.Group) -> tuple[str, str]:
    return display_name(entity.name).lower().replace("ё", "е"), entity.key


NAME_SOURCES = {"model": "дала модель", MANUAL: "исправлено вручную"}


def article_order(article: str) -> tuple[int, ...]:
    """«205.2» after «205» and before «207.3»: by number, not as text."""
    return tuple(int(part) if part.isdigit() else 0 for part in article.split("."))


def _articles_by_group(db: Session, group_ids: Sequence[int]) -> dict[int, list[tuple[str, bool]]]:
    """Per entity of a page, its articles in number order and whether any is its own."""
    articles = identity.charge_articles(db, group_ids)
    for found in articles.values():
        found.sort(key=lambda item: article_order(item[0]))
    return articles


def _strongest(levels: set[str]) -> str:
    """An entity's strongest Rosfinmonitoring match: full, else name."""
    return FULL if FULL in levels else min(levels)


def _rf_levels(db: Session, group_ids: Sequence[int]) -> dict[int, str]:
    """Per entity of a page, its strongest Rosfinmonitoring match."""
    return {
        group_id: _strongest(found)
        for group_id, found in analysis_read.list_levels(db, group_ids).items()
    }


def _roles(db: Session, group_ids: Sequence[int]) -> dict[int, tuple[str, str | None]]:
    return {
        group_id: (found.role, found.kind)
        for group_id, found in identity.roles(db, group_ids).items()
    }


def regions_text(regions: Sequence[Any]) -> str:
    """The regions of a person as plain text, for a cell."""
    return ", ".join(str(region) for region, _count in regions)


def role_label(role: str, kind: str | None) -> str:
    if role == FIGURANT:
        return "фигурант дела"
    if role == POSSIBLE:
        return KIND_LABELS.get(kind or "", "задержан или обыскан")
    if role == MENTIONED:
        return f"упомянут: {KIND_LABELS.get(kind or '', 'другое')}"
    return "не ясно"


ROLE_METHODS = {
    "article": "статья УК в событии",
    "model": "ответ модели по цитатам",
    "official": "должностное лицо",
    "rules": "правило",
    "manual": "ручная пометка",
}


ROLE_FILTERS = {
    "figurant": "Фигуранты дел",
    "all": "Все роли",
    POSSIBLE: "Задержан, обыск, административное",
    MENTIONED: "Только упомянуты",
    UNCLEAR: "Роль неясна",
}

VERDICT_FILTERS = {
    "all": "Любой вердикт",
    POLITICAL: "Политические",
    CRIMINAL: "Обычные уголовные",
    "unclear": "Неясные",
    "none": "Не оценивались",
}


@dataclass(frozen=True)
class EntityPage:
    """One page of «Все люди» under its filters, and what the filters' labels count. Read
    by the legacy page and by `GET /api/v1/entities`."""

    entities: list[identity.Group]
    total: int
    # In the list for certain, and maybe (a namesake), before those are hidden.
    hidden: int
    hidden_possible: int
    # Before step 4 has run nobody has a role, and the role filters nothing.
    roles_known: bool
    charges: dict[int, list[tuple[str, bool]]]
    listed: dict[int, str]
    roles: dict[int, tuple[str, str | None]]
    verdicts: dict[int, str]
    regions: list[str]


def read_entity_page(
    db: Session,
    *,
    q: str,
    article: str,
    rf: str,
    rf_possible: str,
    role: str,
    verdict: str,
    region: str,
    sort: str,
    page: int,
) -> EntityPage:
    """`article` and `region` come stripped; `role` is one of the filters, never empty."""
    # Each owner filters by its own columns: the groups' words and charges here, the
    # analysis's verdicts and list matches below.
    ids = identity.filtered_group_ids(db, q=q, article=article, region=region)
    if verdict != "all":
        verdicts = analysis_read.verdicts(db)
        ids = [
            group_id
            for group_id in ids
            if (
                group_id not in verdicts if verdict == "none" else verdicts.get(group_id) == verdict
            )
        ]
    levels = analysis_read.list_levels(db)
    in_list = {group_id for group_id, found in levels.items() if FULL in found}
    # Given name and surname only: not on the list for certain, maybe a namesake.
    maybe_listed = {group_id for group_id, found in levels.items() if found - {FULL}} - in_list
    hidden = sum(group_id in in_list for group_id in ids)
    hidden_possible = sum(group_id in maybe_listed for group_id in ids)
    if rf == "hide":
        ids = [group_id for group_id in ids if group_id not in in_list]
    if rf_possible == "hide":
        ids = [group_id for group_id in ids if group_id not in maybe_listed]
    # Before step 4 has run nobody has a role: the role then filters nothing.
    roles_known = identity.any_roles(db)
    if role != "all" and roles_known:
        holding = set(identity.groups_with_role(db, FIGURANT if role == "figurant" else role))
        ids = [group_id for group_id in ids if group_id in holding]
    total = len(ids)
    if sort == "name":
        # By the name as shown: the surname is not a column, and ten thousand short rows
        # sort here in milliseconds.
        ordered = sorted(identity.groups(db, ids).values(), key=_surname_key)
        entities = ordered[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]
    else:
        entities = identity.ordered_groups(
            db, ids, order=sort, offset=(page - 1) * PAGE_SIZE, limit=PAGE_SIZE
        )
    page_ids = [entity.id for entity in entities]
    roles = _roles(db, page_ids)
    verdicts_shown = {
        group_id: found.verdict for group_id, found in analysis_read.politics(db, page_ids).items()
    }
    return EntityPage(
        entities=entities,
        total=total,
        hidden=hidden,
        hidden_possible=hidden_possible,
        roles_known=roles_known,
        charges=_articles_by_group(db, page_ids),
        listed={
            group_id: _strongest(levels[group_id]) for group_id in page_ids if group_id in levels
        },
        roles=roles,
        verdicts=verdicts_shown,
        regions=identity.all_regions(db),
    )
