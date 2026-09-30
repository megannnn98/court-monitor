"""Справочник статей: «статьи, которые нас интересуют».

Пятый справочник, первые четыре — источники, перечень, найденные люди и
должностные лица. Читается он из публичной ссылки Airtable так же, как
остальные, и синхронизируется той же кнопкой.

Статья в списке — это номер, а не человек, поэтому в таблице своя строка на
статью, а поле `article_key` хранит только цифры: так «ст. 159», «159 УК РФ»
и «статья 159» находят друг друга. Исходная формулировка хранится рядом —
её читает человек.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from airtable.client import AirtableRecord
from airtable.models import TableSyncResult
from airtable.repository import _same, _write
from db.orm_models import CriminalArticleRecord

logger = logging.getLogger("airtable")

# The columns a list of articles may name them in. The export has one: «Полная статья».
_NAME_FIELDS = (
    "Полная статья",
    "full_article",
    "article",
    "Статья",
    "статья",
    "Название статьи",
    "name",
    "Name",
)

# What the digits are, wherever they are written: «ст. 159 ч. 3 УК РФ», «159 УК РФ».
_DIGITS = re.compile(r"\d+")


def article_key(text: str) -> str:
    """The number of an article as the pipeline writes it: «ст. 159 ч. 3 УК РФ» → «159.3».

    Dotted, because that is how `entity_group_charges.article` reads — «205.2» — and a key
    in another shape would never meet a charge found in the news.

    A part is part of the key, not noise: 159 and 159.3 are different charges, and folding
    them together would report a match that is not one.

    The code is not part of the key, and that is a deliberate limit rather than an
    oversight: the list holds both «ст. 30 УК РФ ч. 1» and «ст. 30 УК ЛНР ч. 1», and they
    share the key «30.1». The charges table carries no code either, so a key with one
    would meet nothing. While nothing matches on this list, that costs only a count;
    whoever wires articles into the pipeline must decide with the pipeline's own data in
    front of them.
    """
    digits = _DIGITS.findall(text or "")
    if not digits:
        return ""
    return ".".join(digits[:2])


def sync_articles(session: Session, records: Sequence[AirtableRecord]) -> TableSyncResult:
    """Upsert the articles worth watching, keyed by the Airtable record id."""
    result = TableSyncResult(received=len(records))
    existing = {
        str(row.external_id): row
        for row in session.scalars(
            select(CriminalArticleRecord).where(
                CriminalArticleRecord.external_id.in_({record.id for record in records})
            )
        )
    }
    for record in records:
        text = record.text(*_NAME_FIELDS)
        if not text:
            result.errors += 1
            logger.warning("event=airtable_article_without_text record=%s", record.id)
            continue
        key = article_key(text)
        if not key:
            # A row with no number in it cannot be matched against a charge, so writing
            # it would be a row nobody could ever use.
            result.errors += 1
            logger.warning(
                "event=airtable_article_without_number record=%s text=%s", record.id, text
            )
            continue
        values: dict[str, Any] = {
            "article_text": text,
            "article_key": key,
            "active": True,
        }
        row = existing.get(record.id)
        if row is None:
            session.add(CriminalArticleRecord(external_id=record.id, **values))
            result.created += 1
        elif _same(row, values):
            result.unchanged += 1
        else:
            _write(row, values)
            result.updated += 1
    session.commit()
    return result
