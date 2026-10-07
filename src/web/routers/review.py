"""The review stations for the React console: unclear roles, unclear politics and the
pairs that may be one person, with the operator's decisions. The decisions are actions,
refused from other origins (`web.csrf`, ADR 0022). Resetting every pair decision is
irreversible and stays on the legacy page."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from db.orm_models import EntityGroupRecord
from web.dependencies import get_db
from web.response_models import (
    DecisionResponse,
    EntityArticleResponse,
    NameFormResponse,
    OptionResponse,
    PairDecisionRequest,
    PairListResponse,
    PairResponse,
    PairSideResponse,
    ReviewItemResponse,
    ReviewListResponse,
    RoleDecisionRequest,
    VerdictDecisionRequest,
)
from web.routers.entities import rf_label
from web.ui.disputes import KIND_HINTS, KINDS, PAGE_SIZE, PairPage, read_pairs, settle_pair
from web.ui.entities import display_name, role_label
from web.ui.queue import (
    POLITICS_EXPLANATION,
    POLITICS_TITLE,
    ROLE_CHOICES,
    ROLES_EXPLANATION,
    ROLES_TITLE,
    VERDICT_CHOICES,
    settle_politics,
    settle_role,
    unclear_roles,
    unclear_verdicts,
)

router = APIRouter()


def _review(
    title: str,
    explanation: str,
    choices: tuple[tuple[str, str], ...],
    rows: list[tuple[str, str, str]],
) -> ReviewListResponse:
    return ReviewListResponse(
        title=title,
        explanation=explanation,
        choices=[OptionResponse(value=value, label=label) for value, label in choices],
        items=[
            ReviewItemResponse(key=key, name=display_name(name), reason=reason)
            for key, name, reason in rows
        ],
    )


@router.get("/review/roles", response_model=ReviewListResponse)
def list_unclear_roles(db: Session = Depends(get_db)) -> ReviewListResponse:  # noqa: B008
    """The people step 4 could not place, most mentioned first."""
    return _review(ROLES_TITLE, ROLES_EXPLANATION, ROLE_CHOICES, unclear_roles(db))


@router.post("/review/roles/decide", response_model=DecisionResponse)
def decide_role(
    body: RoleDecisionRequest,
    db: Session = Depends(get_db),  # noqa: B008
) -> DecisionResponse:
    """A person's word on a role: applied at once and kept for every rebuild."""
    entity = settle_role(db, body.key, body.role)
    return DecisionResponse(key=entity.key, decision=body.role)


@router.get("/review/politics", response_model=ReviewListResponse)
def list_unclear_politics(db: Session = Depends(get_db)) -> ReviewListResponse:  # noqa: B008
    """The cases step 5 could not judge, most mentioned first."""
    return _review(POLITICS_TITLE, POLITICS_EXPLANATION, VERDICT_CHOICES, unclear_verdicts(db))


@router.post("/review/politics/decide", response_model=DecisionResponse)
def decide_politics(
    body: VerdictDecisionRequest,
    db: Session = Depends(get_db),  # noqa: B008
) -> DecisionResponse:
    """A person's word on a case's politics: applied at once and kept for every rebuild."""
    entity = settle_politics(db, body.key, body.verdict)
    return DecisionResponse(key=entity.key, decision=body.verdict)


def _side(entity: EntityGroupRecord, data: PairPage) -> PairSideResponse:
    role = data.roles.get(entity.id)
    return PairSideResponse(
        key=entity.key,
        name=display_name(entity.name),
        role_label=role_label(*role) if role else None,
        rf_label=rf_label(data.listed.get(entity.id)),
        variants=[
            NameFormResponse(form=str(form), count=count) for form, count in entity.variants[:5]
        ],
        mention_count=entity.mention_count,
        article_count=entity.article_count,
        regions=[str(region) for region, _count in entity.regions or []],
        articles=[
            EntityArticleResponse(article=article, shared=not sole)
            for article, sole in data.charges.get(entity.id, [])
        ],
    )


@router.get("/review/pairs", response_model=PairListResponse)
def list_pairs(
    kind: str = Query(default="all", pattern="^(all|patronymic|similar|region)$"),
    page: int = Query(default=1, ge=1),
    key: str = Query(default="", max_length=300),
    db: Session = Depends(get_db),  # noqa: B008
) -> PairListResponse:
    """Two entities that may be one person, fifty a page; `key` brings that person's
    pairs first."""
    data = read_pairs(db, kind=kind, page=page, key=key)
    items = []
    for pair in data.on_page:
        left, right = data.records.get(pair.left.id), data.records.get(pair.right.id)
        if left is None or right is None:
            continue
        items.append(
            PairResponse(
                kind=pair.kind,
                hint=KIND_HINTS[pair.kind],
                note=data.notes.get(pair.keys) or None,
                left=_side(left, data),
                right=_side(right, data),
            )
        )
    return PairListResponse(
        kinds=[
            OptionResponse(
                value=value,
                label=label,
                count=data.counts[value] if value in data.counts else len(data.pairs),
            )
            for value, label in KINDS.items()
        ],
        open_pairs=len(data.pairs),
        items=items,
        total=len(data.shown),
        page=page,
        page_size=PAGE_SIZE,
        decided=data.decided,
    )


@router.post("/review/pairs/decide", response_model=DecisionResponse)
def decide_pair(
    body: PairDecisionRequest,
    db: Session = Depends(get_db),  # noqa: B008
) -> DecisionResponse:
    """«same»: one person, merged now and at every rebuild; «different»: off the list."""
    settle_pair(db, body.key_a, body.key_b, body.decision)
    return DecisionResponse(key=f"{body.key_a}|{body.key_b}", decision=body.decision)
