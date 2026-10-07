"""«Все люди» for the React console: the legacy list's rows, filters and counts. Read-only."""

from urllib.parse import quote

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from db.orm_models import EntityGroupRecord
from entities.overrides import MANUAL
from entities.rf_check import FULL
from web.dependencies import get_db
from web.response_models import (
    EntityArticleResponse,
    EntityListResponse,
    EntityRowResponse,
    EventCountResponse,
    OptionResponse,
)
from web.ui.entities import (
    EVENT_LABELS,
    PAGE_SIZE,
    ROLE_FILTERS,
    VERDICT_FILTERS,
    EntityPage,
    display_name,
    read_entity_page,
    role_label,
)

router = APIRouter()

# The words of the legacy list's marks beside a name.
_NAME_SOURCE_LABELS = {"model": "ИИ", MANUAL: "исправлено"}
_VERDICT_LABELS = {"political": "политическое", "unclear": "политичность неясна"}


def rf_label(level: str | None) -> str | None:
    if level is None:
        return None
    return "в перечне" if level == FULL else "возможно в перечне"


def _row(entity: EntityGroupRecord, data: EntityPage) -> EntityRowResponse:
    role = data.roles.get(entity.id)
    verdict = data.verdicts.get(entity.id)
    level = data.listed.get(entity.id)
    return EntityRowResponse(
        id=entity.id,
        key=entity.key,
        name=display_name(entity.name),
        name_source_label=_NAME_SOURCE_LABELS.get(entity.name_source),
        rf_level=level,
        rf_label=rf_label(level),
        role=role[0] if role else None,
        role_label=role_label(*role) if role else None,
        verdict=verdict,
        verdict_label=_VERDICT_LABELS.get(verdict or ""),
        regions=[str(region) for region, _count in entity.regions],
        articles=[
            EntityArticleResponse(article=article, shared=not sole)
            for article, sole in data.charges.get(entity.id, [])
        ],
        events=[
            EventCountResponse(kind=kind, label=EVENT_LABELS.get(kind, kind), count=count)
            for kind, count in sorted(entity.event_types.items(), key=lambda item: -item[1])
        ],
        mention_count=entity.mention_count,
        article_count=entity.article_count,
        last_published_at=entity.last_published_at,
        variants=[form for form, _ in entity.variants[:3]],
        dossier_url=f"/ui/investigations/{quote(entity.key)}",
    )


@router.get("/entities", response_model=EntityListResponse)
def list_entities(
    q: str = Query(default="", max_length=200),
    article: str = Query(default="", max_length=32),
    rf: str = Query(default="all", pattern="^(hide|all)$"),
    rf_possible: str = Query(default="all", pattern="^(hide|all)$"),
    role: str = Query(default="figurant", pattern="^(figurant|all|possible|mentioned|unclear)$"),
    verdict: str = Query(default="all", pattern="^(all|political|criminal|unclear|none)$"),
    region: str = Query(default="", max_length=200),
    sort: str = Query(default="mentions", pattern="^(mentions|articles|recent|name)$"),
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),  # noqa: B008
) -> EntityListResponse:
    """The people of the publications with a criminal case, as the legacy «Все люди» lists
    them: the same filters, the same order, a hundred a page."""
    data = read_entity_page(
        db,
        q=q,
        article=article.strip(),
        rf=rf,
        rf_possible=rf_possible,
        role=role,
        verdict=verdict,
        region=region.strip(),
        sort=sort,
        page=page,
    )
    return EntityListResponse(
        items=[_row(entity, data) for entity in data.entities],
        total=data.total,
        page=page,
        page_size=PAGE_SIZE,
        hidden_in_list=data.hidden,
        hidden_maybe_listed=data.hidden_possible,
        roles_known=data.roles_known,
        roles=[OptionResponse(value=value, label=label) for value, label in ROLE_FILTERS.items()],
        verdicts=[
            OptionResponse(value=value, label=label) for value, label in VERDICT_FILTERS.items()
        ],
        regions=data.regions,
    )
