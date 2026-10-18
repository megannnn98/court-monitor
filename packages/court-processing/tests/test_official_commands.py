"""Exclusion-list sync changes only the requested records, without committing."""

from typing import cast

from sqlalchemy import Table, create_engine, select
from sqlalchemy.orm import Session

from processing.roles.commands import OfficialDetails, refresh_officials
from processing.storage.models import ExcludedPersonRecord


def test_refresh_officials_upserts_and_obeys_rollback() -> None:
    engine = create_engine("sqlite://")
    try:
        cast(Table, ExcludedPersonRecord.__table__).create(engine)
        with Session(engine) as session:
            session.add_all(
                [
                    ExcludedPersonRecord(
                        external_id=key, full_name=name, normalized_name=name, active=True
                    )
                    for key, name in [("manual", "Manual"), ("old", "Old"), ("same", "Same")]
                ]
            )
            session.commit()
            result = refresh_officials(
                session,
                [
                    OfficialDetails("old", "New", "new", "judge", "reason", False),
                    OfficialDetails("same", "Same", "Same", "other", None, True),
                    OfficialDetails("fresh", "Fresh", "fresh", "other", None, True),
                ],
            )
            assert (result.created, result.updated, result.unchanged) == (1, 1, 1)
            rows = {row.external_id: row for row in session.scalars(select(ExcludedPersonRecord))}
            assert len(rows) == 4
            assert rows["manual"].full_name == "Manual"
            assert not rows["old"].active and rows["old"].category == "judge"
            session.rollback()
            rows = {row.external_id: row for row in session.scalars(select(ExcludedPersonRecord))}
            assert len(rows) == 3 and rows["old"].full_name == "Old"
    finally:
        engine.dispose()
