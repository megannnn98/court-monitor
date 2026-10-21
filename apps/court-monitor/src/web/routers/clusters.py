"""Clusters of publications about one event. Read-only; run `resolve-clusters` to form them."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from cases.clusters import read
from cases.clusters.read import ClusterDetail, ClusterSummary
from web.dependencies import get_db

router = APIRouter()


class ClusterListResponse(BaseModel):
    total: int
    items: list[ClusterSummary]


@router.get("/clusters", response_model=ClusterListResponse)
def list_clusters(
    person_key: str | None = Query(default=None, max_length=255),
    article_id: int | None = Query(default=None, ge=1),
    status: str | None = Query(default=None, max_length=16),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),  # noqa: B008
) -> ClusterListResponse:
    """Clusters, newest first. `person_key` lists the clusters of one person
    (`entity_groups.key`); `article_id` the clusters one publication is in — a publication
    that tells two steps (a charge and the opening of a case) is in two of them;
    `status` filters by candidate, confirmed or rejected."""
    total, items = read.list_clusters(
        db,
        limit=limit,
        offset=offset,
        person_key=person_key,
        article_id=article_id,
        status=status,
    )
    return ClusterListResponse(total=total, items=items)


@router.get("/clusters/{cluster_id}", response_model=ClusterDetail)
def get_cluster(cluster_id: UUID, db: Session = Depends(get_db)) -> ClusterDetail:  # noqa: B008
    """A cluster with its members (the anchor first), the reasons each joined, the
    clusters each was not joined to, and its publications."""
    detail = read.cluster_detail(db, cluster_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Cluster not found")
    return detail
