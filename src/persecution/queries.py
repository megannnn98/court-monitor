"""Shared query building blocks over persecution classifications."""

from __future__ import annotations

from sqlalchemy import Select, func, select

from db.orm_models import PersecutionClassificationRecord


def latest_persecution_classification_ids() -> Select[tuple[int]]:
    """Ids of each person's latest persecution classification.

    A person can hold several classifications (one per classifier
    name/version). Only the most recent one (`classified_at`, then `id`)
    describes the person now — an older POLITICAL record superseded by a
    newer NON_POLITICAL one must not make the person politically persecuted.
    """
    ranked = select(
        PersecutionClassificationRecord.id,
        func.row_number()
        .over(
            partition_by=PersecutionClassificationRecord.person_id,
            order_by=(
                PersecutionClassificationRecord.classified_at.desc(),
                PersecutionClassificationRecord.id.desc(),
            ),
        )
        .label("rank"),
    ).subquery()
    return select(ranked.c.id).where(ranked.c.rank == 1)
