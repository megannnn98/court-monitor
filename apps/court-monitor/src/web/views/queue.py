"""Focused review pages for unclear roles and political classifications.

Both read what steps 4 and 5 were unsure about and let the operator settle it: the
decision is applied at once and kept by the entity key, so the next rebuild of step 4 or
step 5 writes it again (`processing.roles.roles`, `analysis.politics.classification`).
"""

from __future__ import annotations

import logging

from fastapi import HTTPException
from sqlalchemy.orm import Session

from analysis.politics.classification import CRIMINAL, POLITICAL, decide_politics
from analysis.politics.classification import UNCLEAR as UNCLEAR_VERDICT
from application import group_followers
from processing.read import Group, groups_by_key, role_review
from processing.roles.roles import FIGURANT, MENTIONED, POSSIBLE, decide_role
from processing.roles.roles import UNCLEAR as UNCLEAR_ROLE
from results.review import politics_review

logger = logging.getLogger("entities")

LIST_LIMIT = 100


ROLE_CHOICES: tuple[tuple[str, str], ...] = (
    (FIGURANT, "Фигурант"),
    (POSSIBLE, "Задержан или обыскан"),
    (MENTIONED, "Только упомянут"),
)

VERDICT_CHOICES: tuple[tuple[str, str], ...] = (
    (POLITICAL, "Политическое"),
    (CRIMINAL, "Обычное уголовное"),
)

ROLES_TITLE = "Неясная роль в деле"

ROLES_EXPLANATION = (
    "Шаг 4 не понял, заведено ли на человека дело. Проверьте цитаты в досье и решите: "
    "фигурант, задержан или обыскан, либо только упомянут. Решение сохраняется и "
    "применяется при каждой пересборке."
)

POLITICS_TITLE = "Неясная политичность"

POLITICS_EXPLANATION = (
    "Шаг 5 не смог отнести дело ни к политическим, ни к обычным уголовным. Решите по "
    "досье и исходным публикациям. Решение сохраняется и применяется при каждой "
    "пересборке."
)


def unclear_roles(db: Session) -> list[tuple[str, str, str]]:
    """(key, name, why) of the people step 4 could not place, most mentioned first."""
    return role_review(db, role=UNCLEAR_ROLE, limit=LIST_LIMIT)


def unclear_verdicts(db: Session) -> list[tuple[str, str, str]]:
    """(key, name, why) of the cases step 5 could not judge, most mentioned first."""
    return politics_review(db, verdict=UNCLEAR_VERDICT, limit=LIST_LIMIT)


def settle_role(db: Session, key: str, role: str) -> Group:
    """A person's word on an entity's role, committed and kept for every rebuild.
    HTTPException 400/404 for no key, an unknown person or an unknown role."""
    entity = entity_by_key(db, key)
    try:
        decide_role(db, entity, role, downstream=group_followers())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Неизвестная роль") from exc
    db.commit()
    logger.info("event=entity_role_decided key=%s", entity.key)
    return entity


def settle_politics(db: Session, key: str, verdict: str) -> Group:
    """A person's word on a case's politics, committed and kept for every rebuild."""
    entity = entity_by_key(db, key)
    try:
        decide_politics(db, entity, verdict)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Неизвестный вердикт") from exc
    db.commit()
    logger.info("event=entity_politics_decided key=%s", entity.key)
    return entity


def entity_by_key(db: Session, key: str) -> Group:
    if not key:
        raise HTTPException(status_code=400, detail="Не указан человек")
    entity = groups_by_key(db, [key]).get(key)
    if entity is None:
        raise HTTPException(status_code=404, detail="Сущность не найдена")
    return entity
