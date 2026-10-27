"""The officials list, kept by hand in PostgreSQL, taken away as a file.

The list is edited on the React page through `/api/v1/officials`; the rules of an
addition, a switching off and a suggestion live with them (`web.views.officials_list`).
This is the CSV the page links to."""

from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from processing.roles import exclusion_list
from web.dependencies import get_db

router = APIRouter()


@router.get("/ui/airtable/officials.csv")
def officials_csv(db: Session = Depends(get_db)) -> Response:  # noqa: B008
    """The whole list as a file, all rows and not only the page on screen."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["ФИО", "Категория", "Причина", "В силе"])
    for row in exclusion_list.every_official(db):
        writer.writerow(
            [row.full_name, row.category or "", row.reason or "", "да" if row.active else "нет"]
        )
    return Response(
        content=("﻿" + buffer.getvalue()).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="dolzhnostnye-lica.csv"'},
    )
