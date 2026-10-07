"""«Найти человека» and the dossier for the React console. Read-only: the dossier's
manual decisions (the name, «должностное лицо») stay legacy forms until mutations are
authenticated. The graph is read from the existing `/api/investigations/{key}/graph`."""

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from entities.news import KIND_LABELS as NEWS_LABELS
from entities.politics import VERDICT_LABELS
from entities.rf_articles import listing
from entities.rf_check import FULL
from persecution.classifier import POLITICAL_ARTICLES
from rosfinmonitoring.inclusion_dates import ATTRIBUTION as INCLUSION_ATTRIBUTION
from web.dependencies import get_db
from web.response_models import (
    ChargeResponse,
    DossierKnownResponse,
    DossierNewsResponse,
    DossierResponse,
    EventCountResponse,
    EvidenceResponse,
    InvestigationFoundResponse,
    InvestigationSearchResponse,
    NameFormResponse,
    OrgResponse,
    PersonLinkResponse,
    QuoteResponse,
    RelatedPersonResponse,
    RfEntryResponse,
    TimelineItemResponse,
)
from web.ui.dossier import (
    ORG_ROLES,
    TIMELINE_LIMIT,
    VERDICT_METHODS,
    Dossier,
    Source,
    identification,
    load,
    rf_entry_text,
    rf_status,
    search,
    warnings,
)
from web.ui.entities import (
    EVENT_LABELS,
    NAME_SOURCES,
    ROLE_METHODS,
    article_order,
    display_name,
    role_label,
)

router = APIRouter()

_GENDER_LABELS = {"male": "мужчина", "female": "женщина"}


def _person(key: str, name: str) -> PersonLinkResponse:
    return PersonLinkResponse(
        key=key, name=display_name(name), dossier_url=f"/ui/investigations/{quote(key)}"
    )


def _quote(source: Source) -> QuoteResponse:
    return QuoteResponse(
        article_id=source.article_id,
        title=source.title,
        source=source.source,
        published_at=source.published_at,
        quote=source.quote,
        start=source.start,
        end=source.end,
        text_start=source.text_start,
        text_end=source.text_end,
    )


def _known(dossier: Dossier) -> DossierKnownResponse:
    match = dossier.known
    if not dossier.known_loaded or match is None:
        return DossierKnownResponse(
            loaded=dossier.known_loaded,
            not_in_base=dossier.known_loaded,
            label=None,
            names=[],
            more=0,
        )
    return DossierKnownResponse(
        loaded=True,
        not_in_base=False,
        label=match.label,
        names=list(match.names),
        more=max(0, match.count - len(match.names)),
    )


def _dossier(dossier: Dossier) -> DossierResponse:
    entity = dossier.entity
    rf_label, rf_badge = rf_status(dossier.rf)
    listed_full = any(row.level == FULL for row in dossier.rf)
    expected = listing(dossier.charges, FULL if listed_full else None)
    return DossierResponse(
        key=entity.key,
        name=display_name(entity.name),
        role=entity.role,
        role_label=role_label(entity.role, entity.kind) if entity.role else None,
        role_method_label=(
            ROLE_METHODS.get(entity.role_method, entity.role_method) if entity.role else None
        ),
        role_reason=entity.role_reason if entity.role else None,
        role_quote=entity.role_quote or None,
        verdict=entity.verdict,
        verdict_label=VERDICT_LABELS.get(entity.verdict, entity.verdict)
        if entity.verdict
        else None,
        verdict_method_label=(
            VERDICT_METHODS.get(entity.verdict_method, entity.verdict_method)
            if entity.verdict
            else None
        ),
        verdict_reason=entity.verdict_reason if entity.verdict else None,
        verdict_quote=entity.verdict_quote or None,
        verdict_source_article_id=dossier.verdict_source,
        rf_label=rf_label,
        rf_maybe=rf_badge == "pending",
        rf_entries=[
            RfEntryResponse(
                level=row.level,
                level_label="ФИО с отчеством" if row.level == FULL else "имя и фамилия",
                text=rf_entry_text(row),
            )
            for row in dossier.rf
        ],
        rf_expected=expected.text if expected else None,
        snapshot_date=dossier.snapshot_date,
        inclusion_attribution=INCLUSION_ATTRIBUTION,
        disputes=dossier.disputes,
        variants=[NameFormResponse(form=str(form), count=count) for form, count in entity.variants],
        regions=[str(region) for region, _count in entity.regions or []],
        article_count=entity.article_count,
        mention_count=entity.mention_count,
        first_published_at=entity.first_published_at,
        last_published_at=entity.last_published_at,
        events=[
            EventCountResponse(kind=kind, label=EVENT_LABELS.get(kind, kind), count=count)
            for kind, count in sorted((entity.event_types or {}).items(), key=lambda item: -item[1])
        ],
        name_source_label=NAME_SOURCES.get(entity.name_source, "по правилам склейки"),
        gender_label=_GENDER_LABELS.get(entity.gender or ""),
        news=(
            DossierNewsResponse(
                kind=entity.news_kind,
                label=NEWS_LABELS.get(entity.news_kind, entity.news_kind),
                reason=entity.news_reason or "",
            )
            if entity.news_kind
            else None
        ),
        known=_known(dossier),
        warnings=warnings(dossier),
        charges=[
            ChargeResponse(
                article=article,
                parts=sorted(dossier.charges[article]["parts"], key=article_order),
                shared=not dossier.charges[article]["sole"],
                political=article in POLITICAL_ARTICLES,
                publications=len(dossier.charges[article]["publications"]),
            )
            for article in sorted(dossier.charges, key=article_order)
        ],
        timeline=[
            TimelineItemResponse(
                event_type=item.event_type,
                label=EVENT_LABELS.get(item.event_type, item.event_type),
                day=item.day,
                dated=item.dated,
                confidence=item.confidence,
                extractor_label="правилами" if "rule" in item.extractor else item.extractor,
                articles=sorted(item.articles, key=article_order),
                orgs=[
                    OrgResponse(name=name, role_label=ORG_ROLES.get(role, role))
                    for role, name in sorted(item.orgs)
                ],
                sources=[_quote(source) for source in item.sources],
            )
            for item in dossier.timeline
        ],
        timeline_capped=dossier.events_capped,
        timeline_limit=TIMELINE_LIMIT,
        publications=[
            EvidenceResponse(
                article_id=publication.article_id,
                title=publication.title,
                source=publication.source,
                published_at=publication.published_at,
                quote=publication.quote,
                start=publication.start,
                end=publication.end,
                text_start=publication.text_start,
                text_end=publication.text_end,
                url=publication.url,
                identification=identification(publication),
                events=[EVENT_LABELS.get(event, event) for event in sorted(publication.events)],
                articles=sorted(publication.articles, key=article_order),
                others=[
                    _person(key, name)
                    for key, name in sorted(publication.others, key=lambda item: item[1])[:12]
                ],
            )
            for publication in dossier.publications
        ],
        related=[
            RelatedPersonResponse(key=key, name=display_name(name), shared=shared)
            for key, name, shared in dossier.related
        ],
        graph_url=f"/api/investigations/{quote(entity.key, safe='')}/graph",
    )


@router.get("/investigations", response_model=InvestigationSearchResponse)
def search_investigations(
    q: str = Query(default="", max_length=200),
    db: Session = Depends(get_db),  # noqa: B008
) -> InvestigationSearchResponse:
    """By any form of the name, the most mentioned first; with no name, the latest
    political cases. Fifty at most, as on the legacy page."""
    found, roles = search(db, q)
    return InvestigationSearchResponse(
        heading=f"Найдено по «{q.strip()}»" if q.strip() else "Свежие политические дела",
        items=[
            InvestigationFoundResponse(
                key=entity.key,
                name=display_name(entity.name),
                role_label=role_label(*roles[entity.id]) if entity.id in roles else None,
                article_count=entity.article_count,
                last_published_at=entity.last_published_at,
            )
            for entity in found
        ],
    )


@router.get("/investigations/{key}", response_model=DossierResponse)
def get_dossier(
    key: str,
    db: Session = Depends(get_db),  # noqa: B008
) -> DossierResponse:
    """The dossier of one person, as the legacy page reads it."""
    dossier = load(db, key)
    if dossier is None:
        raise HTTPException(status_code=404, detail="Человек не найден")
    return _dossier(dossier)
