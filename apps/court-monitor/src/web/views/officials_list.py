"""The officials list, kept by hand in PostgreSQL.

It is the one reference list that is not read from Airtable: a person is added here
because someone decided they must never become a target figurant, and that decision is
a person's, not a copy. So the console is where it is edited — added, switched off,
looked at, and taken away as a file.

Three rules the code below is built around:

- **Deleting is not removing.** A person is switched off with `active = false`, never
  dropped. The list is a record of what was decided; a row that vanished would take the
  decision with it, and there would be no way to tell «we checked and he is not an
  official» from «nobody ever looked».
- **A name that fits nobody is refused, not written.** If the person is not among the
  entities yet, the row would sit here matching nothing. The answer says so.
- **A name that fits several is refused too.** Two people whose names the same words
  could stand for: excluding the wrong one is worse than excluding neither.

Every write goes through the same check step 4 reads, so what the operator sees on this
page is exactly what the pipeline will act on.

Step 4 puts on the list by itself the officials the texts name by title; the ones only
the model calls officials are not put there — it called an arrested policeman one. They
are offered here, one button each, for a person to accept.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException
from sqlalchemy.orm import Session

from processing.roles import exclusion_list
from processing.roles.roles import KIND_LABELS

logger = logging.getLogger("web.ui.airtable")

PAGE_SIZE = 100


# The categories a row may carry. They are the roles the pipeline already knows, so
# saying "судья" here and "judge" there cannot drift apart into two vocabularies.
CATEGORIES = ("judge", "prosecutor", "police", "official", "lawyer", "witness", "other")


def category_label(value: str) -> str:
    return KIND_LABELS.get(value, "прочее") if value != "other" else "прочее"


def officials_listing(db: Session) -> tuple[list[exclusion_list.ListedOfficial], int]:
    """The rows of the list, with the name each one has actually landed on.

    A row that matches no entity yet is shown too, but with a blank name and a mark
    saying so: it is in the list, and the operator should know it does not yet do
    anything.
    """
    return exclusion_list.listing(db, limit=PAGE_SIZE)


def official_suggestion_items(db: Session) -> list[exclusion_list.Suggestion]:
    """The same suggestions with the names they may be a duplicate of."""
    return exclusion_list.suggestions(db, limit=PAGE_SIZE)


def apply_add(db: Session, full_name: str, category: str, reason: str) -> str:
    """Add a person to the list; "added", or "already" for a name it holds. Commits."""
    if category not in CATEGORIES:
        category = "other"
    try:
        status = exclusion_list.add(db, full_name, category, reason)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="ФИО не указано") from exc
    db.commit()
    return status


def apply_add_suggested(db: Session, key: str) -> str:
    """Accept the model's suggestion; "added" or "already". 404 for a key that is no
    suggestion, so the button cannot put an arbitrary name on the list. Commits."""
    status = exclusion_list.add_suggested(db, key)
    if status is None:
        raise HTTPException(status_code=404, detail="Такого предложения нет")
    db.commit()
    return status


def apply_deactivate(db: Session, external_id: str) -> None:
    """Out of force, the row kept; commits."""
    if not exclusion_list.deactivate(db, external_id):
        raise HTTPException(status_code=404, detail="Человека нет в списке")
    db.commit()
