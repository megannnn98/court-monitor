"""«База Airtable» and «Должностные лица» for the React console, read and changed as the
legacy pages do (`web.ui.airtable`, `web.ui.officials_list`). The sync, the refresh of
the published list and the changes to the officials are actions, refused from other
origins (`web.csrf`)."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from operator_console import OperationConflictError, OperationParameters, OperationRegistry
from web.dependencies import get_db, get_operation_registry
from web.response_models import (
    AirtableOverviewResponse,
    AirtableSyncResponse,
    OfficialAddRequest,
    OfficialChangeResponse,
    OfficialDeactivateRequest,
    OfficialRowResponse,
    OfficialSnapshotResponse,
    OfficialsResponse,
    OfficialSuggestedRequest,
    OfficialSuggestionResponse,
    OptionResponse,
    ReferenceListResponse,
    RunStartedResponse,
)
from web.routers.airtable import sync_airtable
from web.ui.airtable import airtable_overview, official_summary
from web.ui.officials_list import (
    CATEGORIES,
    apply_add,
    apply_add_suggested,
    apply_deactivate,
    category_label,
    official_suggestions,
    officials_listing,
)

router = APIRouter()


@router.get("/airtable", response_model=AirtableOverviewResponse)
def get_airtable(db: Session = Depends(get_db)) -> AirtableOverviewResponse:  # noqa: B008
    """The legacy «База Airtable»: the lists, where they come from, the published list."""
    overview = airtable_overview(db)
    summary = official_summary(db)
    return AirtableOverviewResponse(
        configured=overview.configured,
        reason=overview.reason,
        mode_note=overview.mode_note,
        missing=overview.missing,
        source_column=overview.source_column,
        lists=[
            ReferenceListResponse(name=name, label=label, count=count, source=source)
            for name, label, count, source in overview.inventory
        ],
        official=OfficialSnapshotResponse(
            snapshot_id=summary.snapshot_id,
            snapshot_date=summary.snapshot_date,
            entry_count=summary.entry_count,
            match_count=summary.match_count,
        )
        if summary
        else None,
    )


@router.post("/airtable/sync", response_model=AirtableSyncResponse, responses={409: {}, 503: {}})
def sync_reference_lists(db: Session = Depends(get_db)) -> AirtableSyncResponse:  # noqa: B008
    """The four lists into PostgreSQL, as `POST /api/admin/airtable/sync`."""
    return sync_airtable(db)


@router.post("/airtable/rosfin", response_model=RunStartedResponse, responses={409: {}})
def refresh_rosfin(
    registry: OperationRegistry = Depends(get_operation_registry),  # noqa: B008
) -> RunStartedResponse:
    """Download the published list, a new snapshot only if it changed, and check every
    person against it again: the `check-entities-rosfin` run, in the background."""
    from web.ui.management import _OPERATION

    try:
        run = registry.start(_OPERATION, OperationParameters(mode="rosfin"))
    except OperationConflictError as exc:
        raise HTTPException(status_code=409, detail="Идёт другой запуск.") from exc
    return RunStartedResponse(run_id=run.id)


@router.get("/officials", response_model=OfficialsResponse)
def get_officials(db: Session = Depends(get_db)) -> OfficialsResponse:  # noqa: B008
    """The officials list as it stands, and the people the model calls officials."""
    rows, total = officials_listing(db)
    return OfficialsResponse(
        total=total,
        rows=[
            OfficialRowResponse(
                external_id=row.external_id,
                full_name=row.full_name,
                entity_key=key,
                entity_name=name,
                category=category_label(row.category or ""),
                reason=row.reason,
                active=row.active,
            )
            for row, key, name in rows
        ],
        suggestions=[
            OfficialSuggestionResponse(
                key=key, name=name, category=category_label(kind), reason=reason
            )
            for key, name, kind, reason in official_suggestions(db)
        ],
        categories=[
            OptionResponse(value=value, label=category_label(value)) for value in CATEGORIES
        ],
    )


@router.post("/officials/add", response_model=OfficialChangeResponse)
def add_official(
    body: OfficialAddRequest,
    db: Session = Depends(get_db),  # noqa: B008
) -> OfficialChangeResponse:
    return OfficialChangeResponse(status=apply_add(db, body.full_name, body.category, body.reason))


@router.post("/officials/add-suggested", response_model=OfficialChangeResponse)
def add_suggested_official(
    body: OfficialSuggestedRequest,
    db: Session = Depends(get_db),  # noqa: B008
) -> OfficialChangeResponse:
    return OfficialChangeResponse(status=apply_add_suggested(db, body.key))


@router.post("/officials/deactivate", response_model=OfficialChangeResponse)
def deactivate_official(
    body: OfficialDeactivateRequest,
    db: Session = Depends(get_db),  # noqa: B008
) -> OfficialChangeResponse:
    apply_deactivate(db, body.external_id)
    return OfficialChangeResponse(status="deactivated")
