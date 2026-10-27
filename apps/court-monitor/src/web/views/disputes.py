"""«Спорные случаи»: two entities that may be one person, side by side, for a person to
decide. «Один человек» merges them at once and at every rebuild; «Разные люди» takes
the pair off the list."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from analysis.read import list_levels
from analysis.rosfinmonitoring.rf_check import FULL
from application import build_identity_commands
from processing.identity.service import (
    DIFFERENT,
    NAME_PATRONYMIC,
    PATRONYMIC,
    REGION,
    SAME,
    SIMILAR,
    EntityRef,
    Pair,
    key_region,
)
from processing.read import Group, group_references, groups
from web.views.entities import _articles_by_group, _rf_levels, _roles, display_name

PAGE_SIZE = 50

KINDS = {
    "all": "Все",
    PATRONYMIC: "С отчеством и без",
    NAME_PATRONYMIC: "Имя и отчество",
    SIMILAR: "Похожее имя",
    REGION: "Регион у одной",
}

KIND_HINTS = {
    PATRONYMIC: "одно имя и фамилия, у одной сущности есть отчество, у другой нет",
    NAME_PATRONYMIC: "имя и отчество без фамилии и то же с фамилией: решает человек, сами не сливаются",
    SIMILAR: "одна фамилия, имена отличаются окончанием («Лида» и «Лидия»)",
    REGION: "одно ФИО с отчеством; у одной сущности регион из карточки реестра, у другой нет",
}


@dataclass(frozen=True)
class PairPage:
    """«Пары» under its kind: every open pair, the page of them with their entities, and
    the counts. Read by the legacy page and `GET /api/v1/review/pairs`."""

    pairs: list[Pair]
    counts: dict[str, int]
    # Why a pair was left to a person, by its keys.
    notes: dict[tuple[str, str], str]
    shown: list[Pair]
    on_page: list[Pair]
    records: dict[int, Group]
    roles: dict[int, tuple[str, str | None]]
    listed: dict[int, str]
    charges: dict[int, list[tuple[str, bool]]]
    decided: dict[str, int]


def read_pairs(db: Session, *, kind: str, page: int, key: str = "") -> PairPage:
    """`key` brings that person's pairs first."""
    refs = group_references(db)
    pairs = build_identity_commands().disputed_pairs(db, refs)
    if key:
        pairs.sort(key=lambda pair: key not in pair.keys)
    counts = {
        name: sum(pair.kind == name for pair in pairs)
        for name in (PATRONYMIC, NAME_PATRONYMIC, SIMILAR, REGION)
    }
    shown = [pair for pair in pairs if kind == "all" or pair.kind == kind]
    on_page = shown[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]
    ids = [ref.id for pair in on_page for ref in (pair.left, pair.right)]
    records = groups(db, ids)
    return PairPage(
        pairs=pairs,
        counts=counts,
        notes=_why_not_merged(db, pairs),
        shown=shown,
        on_page=on_page,
        records=records,
        roles=_roles(db, ids),
        listed=_rf_levels(db, ids),
        charges=_articles_by_group(db, ids),
        decided=build_identity_commands().decision_counts(db),
    )


def settle_pair(db: Session, key_a: str, key_b: str, decision: str) -> None:
    """«Один человек» or «Разные люди», committed; HTTPException 400 for a half decision."""
    if not key_a or not key_b or key_a == key_b or decision not in (SAME, DIFFERENT):
        raise HTTPException(status_code=400, detail="Неполное решение")
    build_identity_commands().decide_pair(db, key_a, key_b, decision)
    db.commit()


def _candidate(ref: EntityRef, regions: dict[int, list[Any]]) -> str:
    """A name with its card's region: three «Денис Владимирович Попов» differ by it."""
    region = str(regions[ref.id][0][0]) if key_region(ref.key) and regions.get(ref.id) else ""
    return f"«{display_name(ref.name)}»" + (f" ({region})" if region else "")


def _why_not_merged(db: Session, pairs: list[Pair]) -> dict[tuple[str, str], str]:
    """Why a pair was left to a person: a side could be any of several people (the check
    merges a pair only when it is the one)."""
    listed = {group_id for group_id, levels in list_levels(db).items() if FULL in levels}
    # The check leaves a name and a patronymic to a person and does not count it: nor do the
    # notes, or a clear pair would be said to wait for a choice it does not need.
    pairs = [pair for pair in pairs if pair.kind != NAME_PATRONYMIC]
    partners: dict[int, list[EntityRef]] = {}
    for pair in pairs:
        partners.setdefault(pair.left.id, []).append(pair.right)
        partners.setdefault(pair.right.id, []).append(pair.left)
    ambiguous = [ref.id for refs in partners.values() if len(refs) > 1 for ref in refs]
    regions = {group.id: list(group.regions) for group in groups(db, ambiguous).values()}
    notes: dict[tuple[str, str], str] = {}
    for pair in pairs:
        side = next((ref for ref in (pair.left, pair.right) if len(partners[ref.id]) > 1), None)
        if side is None:
            continue
        on_list = (
            ", хотя одна сторона в перечне"
            if pair.left.id in listed or pair.right.id in listed
            else ""
        )
        names = ", ".join(_candidate(ref, regions) for ref in partners[side.id])
        notes[pair.keys] = (
            f"Не слито автоматически{on_list}: «{display_name(side.name)}» подходит сразу "
            f"к нескольким людям — {names}. Решите, кто это."
        )
    return notes
