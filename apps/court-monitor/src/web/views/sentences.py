"""«Приговоры»: the sentences a model read from the articles (`processing.evidence.sentences`),
folded into cases (`results.queries.sentence_cases`) — what «Спросить» counts over.

Each case: who, where, what punishment, for what, the article's own words, and a
person's way to take a wrong one out of every count («Неверно»). What was taken out is
a list of its own, to be put back.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from processing.evidence.regions import canonical
from processing.evidence.sentences import set_sentence_hidden
from processing.read import HiddenSentence, hidden_sentences, sentence_reading_count
from results.ask import REASON_LABELS
from results.queries.sentence_cases import POLITICAL, Case, Filters, cases
from results.queries.sentence_cases import select as select_cases

PAGE_SIZE = 50

HIDDEN = "hidden"


def term_text(months: int, fine_rub: int) -> str:
    """«2 г. 6 мес.», «штраф 35 000 ₽», or a dash."""
    years, rest = divmod(months, 12)
    parts = [f"{years} г." if years else "", f"{rest} мес." if rest else ""]
    if fine_rub:
        parts.append(f"штраф {fine_rub:,} ₽".replace(",", " "))
    return " ".join(part for part in parts if part) or "—"


@dataclass(frozen=True)
class SentencePage:
    """«Приговоры» under its filters; read by the legacy page and `GET /api/v1/sentences`."""

    # The filters as understood: an unknown reason is «political», a region canonical.
    reason: str
    region: str
    all_cases: list[Case]
    # The cases of the page, newest sentence first, and how many pass the filters.
    cases: list[Case]
    total: int
    # The rows a person took out of every count, newest first.
    hidden: Sequence[HiddenSentence]
    # How many publications the model has read for a sentence.
    read: int


def read_sentences(db: Session, *, reason: str, region: str, page: int) -> SentencePage:
    all_cases = cases(db)
    reason = reason if reason in ("", POLITICAL, *REASON_LABELS) else POLITICAL
    region = canonical(region)
    found = select_cases(
        all_cases, Filters(reasons=(reason,) if reason else (), region=region)
    ).cases
    found.sort(key=lambda case: (case.sentenced_on, case.row_ids), reverse=True)
    return SentencePage(
        reason=reason,
        region=region,
        all_cases=all_cases,
        cases=found[(page - 1) * PAGE_SIZE : page * PAGE_SIZE],
        total=len(found),
        hidden=hidden_sentences(db),
        read=sentence_reading_count(db),
    )


async def _set_hidden(request: Request, db: Session, hidden: bool) -> RedirectResponse:
    form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
    ids = {int(part) for part in form.get("rows", [""])[0].split(",") if part.isdigit()}
    if not ids:
        raise HTTPException(status_code=400, detail="Не указан приговор")
    changed = set_sentence_hidden(db, ids, hidden=hidden)
    if not changed:
        raise HTTPException(status_code=404, detail="Такого приговора нет")
    if len(changed) != len(ids):
        # The page is older than the rows: a part of a case must not go alone. Nothing
        # is committed, so nothing is hidden.
        raise HTTPException(
            status_code=409, detail="Список приговоров изменился. Обновите страницу."
        )
    db.commit()
    kept = parse_qs(form.get("back", [""])[0])
    params: dict[str, str] = {
        key: kept[key][0] for key in ("reason", "region", "view", "page") if key in kept
    }
    return RedirectResponse(f"/ui/sentences?{urlencode(params)}", status_code=303)
