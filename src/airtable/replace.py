"""Replacing a list that is a copy of an Airtable view, and proving the copy is whole.

A list read from a public share view is not a log of what the operator wrote down; it is
a copy of what the view says today. When the view loses a row, the copy must lose it too
— otherwise a person removed from Airtable stays in the database forever, and the list
quietly becomes something nobody can reason about. That is the opposite of what the
official list and the operator's own tables get, and it is deliberate: those are records
of decisions, these are a view of someone else's table.

The whole point of this module is that a copy may only be replaced by a *complete* one.
Cleaning up on the strength of a download that turned out to be short is how 13 765
people disappear, and it cannot be undone from the console. So before anything is
removed:

- the transfer must have arrived whole — a body shorter than the server promised is a
  failed read, not a small list;
- the CSV must have a header, since without one the columns are unknown and the rows
  are unreadable rather than empty;
- the row count must not have collapsed against the count we last recorded for this
  list. Airtable publishes no expected total for a public view — there is nowhere in the
  page, the headers or the export to read one from — so this is a plausibility guard
  against a truncated or mis-scoped export, not proof of completeness.

And all of it happens in the caller's transaction: the write and the removal are one
unit of work, so a failure anywhere leaves the previous list standing whole.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

logger = logging.getLogger("airtable")

# A list may shrink, but not to a fraction of itself without someone saying so. Removing
# a person from Airtable is a deliberate act; losing a whole export to a bad link is not,
# and the two look identical if the only rule is "whatever came in, wins".
#
# The floor is deliberately low — a quarter — because a real list can be cut hard (people
# are removed in batches after an amnesty, a case is closed for a hundred names at once).
# It is a floor for the catastrophic case, not a measure of completeness.
MINIMUM_SHARE_OF_PREVIOUS = 0.25

# Below this many rows a list is not believable as a full copy of a working view, whatever
# it was before: an empty or one-line export is a mistake, not a purge.
MINIMUM_ROWS_TO_TRUST = 100


class IncompleteDownload(Exception):
    """The download is not a complete copy, so the list it would replace must stand.

    Carries the reason in Russian because it goes straight to the operator on the page.
    """


def download_looks_complete(body: bytes, content_length: int | None) -> None:
    """Whether the transfer itself arrived whole.

    `content_length` is what the server said it was sending. A body shorter than that is a
    read cut off part way, whatever the status code said.
    """
    if content_length and len(body) < content_length:
        raise IncompleteDownload(
            f"выгрузка пришла не полностью: {len(body)} байт из {content_length}. Список не тронут."
        )


def removal_is_safe(new_rows: int, previous_rows: int) -> tuple[bool, str]:
    """Whether it is safe to drop the rows this list no longer has.

    `previous_rows` is how many rows the list held after the last successful sync, so a
    collapse is measured against what we ourselves last wrote, not against a guess.
    """
    if new_rows < MINIMUM_ROWS_TO_TRUST:
        return False, (
            f"выгрузка пришла с {new_rows} строками — это меньше, чем бывает у списка "
            f"(минимум {MINIMUM_ROWS_TO_TRUST}). Старый список оставлен как был; если "
            "список опустошён намеренно, скажите об этом — это делается вручную."
        )
    if previous_rows and new_rows < previous_rows * MINIMUM_SHARE_OF_PREVIOUS:
        return False, (
            f"выгрузка пришла с {new_rows} строками против {previous_rows} в прошлый раз — "
            "это похоже на оборванную выгрузку, а не на правку списка. "
            "Старый список оставлен как был."
        )
    return True, ""


def drop_vanished(session: Session, model: type[Any], keep: set[str]) -> int:
    """Remove the rows of this list that the export no longer names, in this transaction.

    Only rows carrying this source's ids are ever considered: `sources` and the
    console-owned officials never pass through here, and a row the operator wrote by hand
    has an id this list never produces, so it cannot be swept up by accident.
    """
    total = int(session.scalar(select(func.count()).select_from(model)) or 0)
    if not keep:
        # Every row is gone from the view. That is only believable for a list small
        # enough that removal is plausible; the guard has already refused a big collapse,
        # so reaching here means the list really is empty.
        removed = total
        session.execute(delete(model))
        return removed
    stale = [
        row_id
        for (row_id,) in session.execute(select(model.id).where(model.external_id.not_in(keep)))
    ]
    if not stale:
        return 0
    session.execute(delete(model).where(model.id.in_(stale)))
    logger.info(
        "event=airtable_vanished_removed table=%s removed=%d", model.__tablename__, len(stale)
    )
    return len(stale)
