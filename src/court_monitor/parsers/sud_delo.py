"""Parser for sudrf.ru case cards (sud_delo module).

Extracts structured data from case cards including:
- Case number, UID
- Dates (received, published)
- Judge information
- Persons involved with articles
- Case movement events
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime

from selectolax.parser import HTMLParser

PARSER_VERSION = "sud-delo-0.1"

# Regex patterns
_CASE_NUMBER_RE = re.compile(r"ДЕЛО\s*№\s*([^\n]+)")
_DATE_RE = re.compile(r"(\d{2}\.\d{2}\.\d{4})")
_PUBLISH_DATE_RE = re.compile(r"опубликовано\s+(\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2})")


@dataclass
class CasePerson:
    """Person involved in a case."""

    name: str
    articles: list[str] = field(default_factory=list)
    material: str | None = None
    result: str | None = None


@dataclass
class CaseEvent:
    """Event in case movement."""

    event_type: str
    event_date: date | None = None
    event_time: str | None = None
    result: str | None = None
    location: str | None = None


@dataclass
class ParsedCaseCard:
    """Parsed case card data."""

    case_number: str | None = None
    case_uid: str | None = None
    court: str | None = None
    received_at: date | None = None
    judge: str | None = None
    first_instance_court: str | None = None
    first_instance_case_number: str | None = None
    first_instance_judge: str | None = None
    persons: list[CasePerson] = field(default_factory=list)
    events: list[CaseEvent] = field(default_factory=list)
    published_at: datetime | None = None
    modified_at: datetime | None = None
    parser_version: str = PARSER_VERSION


def parse_case_card(
    html: str,
    case_uid: str | None = None,
    court: str | None = None,
) -> ParsedCaseCard:
    """Parse a sud_delo case card HTML page.

    Args:
        html: HTML content of the case card
        case_uid: Optional case UID from URL
        court: Court name (provenance — which court's website this card came from)

    Returns:
        ParsedCaseCard with extracted data
    """
    tree = HTMLParser(html)
    result = ParsedCaseCard(case_uid=case_uid, court=court)

    # Extract case number
    case_number_node = tree.css_first(".casenumber")
    if case_number_node:
        text = case_number_node.text(strip=True)
        match = _CASE_NUMBER_RE.search(text)
        if match:
            result.case_number = match.group(1).strip()

    # Extract from cont1 (ДЕЛО tab)
    cont1 = tree.css_first("#cont1")
    if cont1:
        _parse_case_info(cont1, result)

    # Extract from cont2 (РАССМОТРЕНИЕ В НИЖЕСТОЯЩЕМ СУДЕ tab)
    cont2 = tree.css_first("#cont2")
    if cont2:
        _parse_first_instance(cont2, result)

    # Extract from cont3 (ДВИЖЕНИЕ ДЕЛА tab)
    cont3 = tree.css_first("#cont3")
    if cont3:
        result.events = _parse_case_movement(cont3)

    # Extract from cont4 (ЛИЦА tab)
    cont4 = tree.css_first("#cont4")
    if cont4:
        result.persons = _parse_persons(cont4)

    # Extract publication info
    publish_node = tree.css_first(".outputArea.publishInfo")
    if publish_node:
        _parse_publish_info(publish_node, result)

    return result


def _parse_case_info(cont, result: ParsedCaseCard) -> None:
    """Parse case info from cont1 (ДЕЛО tab)."""
    rows = cont.css("tr")
    for row in rows:
        cells = row.css("td")
        if len(cells) >= 2:
            # Get label text, stripping nested <b> tags
            label_node = cells[0].css_first("b")
            label = label_node.text(strip=True) if label_node else cells[0].text(strip=True)
            value = cells[1].text(strip=True)

            if "Дата поступления" in label:
                match = _DATE_RE.search(value)
                if match:
                    result.received_at = _parse_date(match.group(1))
            elif "Судья" in label and "первой инстанции" not in label:
                result.judge = value


def _parse_first_instance(cont, result: ParsedCaseCard) -> None:
    """Parse first instance info from cont2."""
    rows = cont.css("tr")
    for row in rows:
        cells = row.css("td")
        if len(cells) >= 2:
            # Get label text, stripping nested <b> tags
            label_node = cells[0].css_first("b")
            label = label_node.text(strip=True) if label_node else cells[0].text(strip=True)
            value = cells[1].text(strip=True)

            # Check more specific conditions first
            if "Судья" in label and "первой инстанции" in label:
                result.first_instance_judge = value
            elif "Номер дела в первой инстанции" in label:
                result.first_instance_case_number = value
            elif "Суд" in label and "первой инстанции" in label and "Судья" not in label:
                result.first_instance_court = value


def _parse_case_movement(cont) -> list[CaseEvent]:
    """Parse case movement events from cont3."""
    events = []
    rows = cont.css("tr")

    # Skip header rows (contain <th> or have header text)
    for row in rows:
        # Skip if row contains header cells
        if row.css("th"):
            continue

        cells = row.css("td")
        if len(cells) >= 4:
            event_type = cells[0].text(strip=True)
            # Skip header row
            if "Наименование события" in event_type:
                continue

            date_str = cells[1].text(strip=True)
            time_str = cells[2].text(strip=True)
            location = cells[3].text(strip=True)
            event_result = cells[4].text(strip=True) if len(cells) > 4 else None

            event_date = None
            match = _DATE_RE.search(date_str)
            if match:
                event_date = _parse_date(match.group(1))

            events.append(
                CaseEvent(
                    event_type=event_type,
                    event_date=event_date,
                    event_time=time_str if time_str else None,
                    location=location if location else None,
                    result=event_result if event_result else None,
                )
            )

    return events


def _parse_persons(cont) -> list[CasePerson]:
    """Parse persons from cont4."""
    persons = []
    rows = cont.css("tr")

    # Skip header rows (contain <th> or have header text)
    for row in rows:
        # Skip if row contains header cells
        if row.css("th"):
            continue

        cells = row.css("td")
        if len(cells) >= 2:
            name = cells[0].text(strip=True)
            # Skip header row
            if "Фамилия / наименование" in name:
                continue

            articles_str = cells[1].text(strip=True)
            material = cells[2].text(strip=True) if len(cells) > 2 else None
            result = cells[3].text(strip=True) if len(cells) > 3 else None

            # Parse articles (e.g., "ст.111 ч.1 УК РФ")
            articles = _parse_articles(articles_str)

            if name:
                persons.append(
                    CasePerson(
                        name=name,
                        articles=articles,
                        material=material if material else None,
                        result=result if result else None,
                    )
                )

    return persons


def _parse_articles(text: str) -> list[str]:
    """Parse articles from text like 'ст.111 ч.1 УК РФ'."""
    if not text:
        return []

    # Split by semicolon (standard delimiter in ГАС «Правосудие»)
    parts = text.split(";")
    articles = []
    for part in parts:
        stripped = part.strip()
        if stripped:
            articles.append(stripped)
    return articles


def _parse_publish_info(node, result: ParsedCaseCard) -> None:
    """Parse publication info."""
    text = node.text(strip=True)

    # Extract published date
    match = _PUBLISH_DATE_RE.search(text)
    if match:
        result.published_at = _parse_datetime(match.group(1))

    # Extract modified date
    modified_match = re.search(r"изменено\s+(\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2})", text)
    if modified_match:
        result.modified_at = _parse_datetime(modified_match.group(1))


def _parse_date(date_str: str) -> date | None:
    """Parse date from DD.MM.YYYY format."""
    try:
        parts = date_str.split(".")
        if len(parts) == 3:
            day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
            return date(year, month, day)
    except (ValueError, IndexError):
        pass
    return None


def _parse_datetime(datetime_str: str) -> datetime | None:
    """Parse datetime from DD.MM.YYYY HH:MM format."""
    try:
        parts = datetime_str.split()
        if len(parts) == 2:
            date_part, time_part = parts
            date_obj = _parse_date(date_part)
            if date_obj:
                time_parts = time_part.split(":")
                if len(time_parts) == 2:
                    hour, minute = int(time_parts[0]), int(time_parts[1])
                    return datetime(date_obj.year, date_obj.month, date_obj.day, hour, minute)
    except (ValueError, IndexError):
        pass
    return None
