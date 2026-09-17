"""A figurant card as an article the extraction pipeline reads like any other.

The card is structured, so the text is written from its fields in the shapes the rule
extractors already know: the full name as the registry writes it (surname first), the
articles as «ч. 2 ст. 205.2 УК РФ», and a charge or a verdict as the one event whose
target is that name. Nothing is guessed: a field the taxonomy cannot read is left out.
"""

from __future__ import annotations

import html
import json
import re
from datetime import UTC, datetime

from sources.ingestion_errors import ParseError
from sources.memopzk import taxonomy
from sources.models import ParsedArticle, RawDocument

REGISTRY_TITLE = "Поддержка политзаключённых. Мемориал"


class FigurantParser:
    def parse(self, raw: RawDocument) -> ParsedArticle:
        try:
            card = json.loads(raw.content)
            title = html.unescape(str(card["title"]["rendered"]))
            classes = [str(item) for item in card.get("class_list", [])]
        except (ValueError, KeyError, TypeError) as exc:
            raise ParseError("Unreadable figurant card") from exc
        # «Мандрыгина (Шелковникова) Евгения Михайловна»: the former name is left out.
        name = " ".join(re.sub(r"\([^)]*\)", " ", title).split())
        # A surname with initials names a person («Сизов А. В.»); initials alone do not.
        words = name.split()
        if len(words) < 2 or not any(len(word.rstrip(".")) > 1 for word in words):
            raise ParseError("Figurant card without a full name")
        return ParsedArticle(
            external_id=raw.external_id,
            url=raw.url,
            title=name,
            published_at=_published_at(card),
            text=figurant_text(name, classes),
        )


def figurant_text(name: str, classes: list[str]) -> str:
    female = taxonomy.FEMALE in classes
    stages = taxonomy.terms(classes, "stage-")
    articles = [
        reference
        for slug in taxonomy.terms(classes, "")
        for reference in taxonomy.legal_references(slug)
    ]
    sentences = [f"{name}."]
    regions = [
        taxonomy.REGIONS[slug]
        for slug in taxonomy.terms(classes, "regions-")
        if slug in taxonomy.REGIONS
    ]
    if regions:
        sentences.append(f"Регион: {', '.join(regions)}.")
    sentenced = any(stage in taxonomy.SENTENCED_STAGES for stage in stages)
    verb = ("осуждена" if female else "осужден") if sentenced else "обвиняется"
    # Without a readable article the card still states the case against the person.
    sentences.append(
        f"{name} {verb} по статьям: {', '.join(articles)}." if articles else f"{name} {verb}."
    )
    repressions = [
        taxonomy.REPRESSIONS[slug]
        for slug in taxonomy.terms(classes, "repression-")
        if slug in taxonomy.REPRESSIONS
    ]
    if repressions:
        sentences.append(f"Мера: {', '.join(repressions)}.")
    categories = [
        taxonomy.CATEGORIES[slug]
        for slug in taxonomy.terms(classes, "category-")
        if slug in taxonomy.CATEGORIES
    ]
    if categories:
        sentences.append(f"Категория дела: {', '.join(categories)}.")
    readable_stages = [taxonomy.STAGES[slug] for slug in stages if slug in taxonomy.STAGES]
    if readable_stages:
        sentences.append(f"Стадия преследования: {', '.join(readable_stages)}.")
    lists = [
        taxonomy.LISTS[slug]
        for slug in taxonomy.terms(classes, "list-persecuted-")
        if slug in taxonomy.LISTS
    ]
    sentences.append(
        f"Проект «{REGISTRY_TITLE}» внёс человека в реестр преследуемых"
        + (f": {'; '.join(f'«{item}»' for item in lists)}." if lists else ".")
    )
    return "\n".join(sentences)


def _published_at(card: dict[str, object]) -> datetime | None:
    value = card.get("date_gmt")
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value).replace(tzinfo=UTC)
    except ValueError:
        return None
