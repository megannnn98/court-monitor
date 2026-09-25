"""«Должностные лица»: the judges, prosecutors, investigators and officials among the
entities. Named in cases, never their figurants: they stay off «Список». A wrong one is
unmarked here, a missing one marked on its card."""

from __future__ import annotations

from html import escape
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db.orm_models import EntityGroupRecord, EntityGroupRoleRecord
from entities.officials import OFFICIAL_KINDS
from entities.roles import KIND_LABELS
from web.dependencies import get_db
from web.ui.entities import _ROLE_METHODS, _regions, display_name
from web.ui.layout import _page

router = APIRouter()

PAGE_SIZE = 100
_KINDS = {"all": "Все", **{kind: KIND_LABELS[kind] for kind in sorted(OFFICIAL_KINDS)}}


@router.get("/ui/officials", response_class=HTMLResponse)
def ui_officials(
    kind: str = Query(default="all", pattern="^(all|judge|official|police|prosecutor)$"),
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    kinds = sorted(OFFICIAL_KINDS) if kind == "all" else [kind]
    query = (
        select(EntityGroupRecord, EntityGroupRoleRecord)
        .join(EntityGroupRoleRecord, EntityGroupRoleRecord.group_id == EntityGroupRecord.id)
        .where(EntityGroupRoleRecord.kind.in_(kinds))
    )
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    counts: dict[str, int] = {
        str(found_kind): int(count)
        for found_kind, count in db.execute(
            select(EntityGroupRoleRecord.kind, func.count())
            .where(EntityGroupRoleRecord.kind.in_(OFFICIAL_KINDS))
            .group_by(EntityGroupRoleRecord.kind)
        ).all()
    }
    found = db.execute(
        query.order_by(EntityGroupRecord.mention_count.desc(), EntityGroupRecord.key)
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
    ).all()
    rows = "".join(
        f"<tr><td>{position}</td>"
        f'<td><a href="/ui/entities/{quote(entity.key)}">{escape(display_name(entity.name))}</a>'
        f'<br><span class="muted">{_regions(entity.regions)}</span></td>'
        f"<td>{escape(KIND_LABELS.get(role.kind or '', ''))}</td>"
        f"<td>{escape(_ROLE_METHODS.get(role.method, role.method))}</td>"
        f"<td>{escape(role.reason)}</td>"
        f'<td class="num">{entity.mention_count}</td>'
        f'<td><form method="post" action="/ui/entities/{quote(entity.key)}/official">'
        '<input type="hidden" name="official" value="no">'
        '<input type="hidden" name="back" value="officials">'
        '<button type="submit" class="secondary">Не должностное лицо</button></form></td></tr>'
        for position, (entity, role) in enumerate(found, start=(page - 1) * PAGE_SIZE + 1)
    )
    chips = " ".join(
        f'<a class="chip{" active" if key == kind else ""}" '
        f'href="/ui/officials?{urlencode({"kind": key})}">{label}'
        f"{f' ({counts.get(key, 0)})' if key != 'all' else f' ({sum(counts.values())})'}</a>"
        for key, label in _KINDS.items()
    )
    pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    pager = " ".join(
        f'<a href="/ui/officials?{urlencode({"kind": kind, "page": number})}">'
        f"{'<b>' + str(number) + '</b>' if number == page else number}</a>"
        for number in range(1, pages + 1)
    )
    body = f"""<p class="chips">{chips}</p>
<p class="muted">Найдено: {total}. Должностное лицо определяет должность перед именем в текстах
(«судья Ольга Минакова», «глава СК Александр Бастрыкин»), ответ модели или ручная пометка.
Депутаты сюда не входят: оппозиционные депутаты сами бывают преследуемыми.</p>
<table><thead><tr><th>№</th><th>Фамилия Имя</th><th>Должность</th><th>Как определено</th>
<th>Почему</th><th>Упоминаний</th><th></th></tr></thead><tbody>{rows}</tbody></table>
<p class="pager">{pager if pages > 1 else ""}</p>"""
    return _page(
        "Должностные лица",
        body,
        active="officials",
        instruction=(
            "Судьи, прокуроры, следователи и чиновники из публикаций: они упоминаются в делах, "
            "но не фигуранты и в «Список» не попадают."
        ),
        next_action=(
            "Снимите пометку с ошибочно попавшего; недостающего отметьте кнопкой в его "
            "карточке в «Сущностях»."
        ),
        db=db,
    )
