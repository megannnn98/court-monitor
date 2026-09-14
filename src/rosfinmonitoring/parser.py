"""Parser for Rosfinmonitoring data."""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import UTC, datetime
from typing import Any

from rosfinmonitoring.models import RosfinmonitoringEntry, RosfinmonitoringEntryStatus


def _normalize_name(full_name: str) -> str:
    """Normalize a person's name for matching."""
    name = full_name.strip().lower()
    name = re.sub(r"[ё]", "е", name)
    name = re.sub(r"[\s\-\.]+", " ", name)
    name = re.sub(r"[^а-яa-z\s]", "", name)
    return name.strip()


def _create_matching_key(normalized_name: str) -> str:
    """Create a matching key from normalized name."""
    return re.sub(r"\s+", "", normalized_name)


def _parse_date(date_str: str | None) -> datetime | None:
    """Parse a date string in various formats."""
    if not date_str:
        return None

    date_str = date_str.strip()

    formats = [
        "%d.%m.%Y",
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%d-%m-%Y",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue

    return None


class CsvRosfinmonitoringParser:
    """Parser for CSV format Rosfinmonitoring data."""

    def parse(self, raw_content: bytes) -> list[RosfinmonitoringEntry]:
        """Parse CSV content into RosfinmonitoringEntry objects."""
        text = raw_content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))

        entries: list[RosfinmonitoringEntry] = []

        for row in reader:
            full_name = row.get("full_name", row.get("ФИО", row.get("name", "")))
            if not full_name:
                continue

            normalized_name = _normalize_name(full_name)
            matching_key = _create_matching_key(normalized_name)

            entry = RosfinmonitoringEntry(
                snapshot_id=0,
                full_name=full_name.strip(),
                normalized_name=normalized_name,
                matching_key=matching_key,
                birth_date=_parse_date(row.get("birth_date", row.get("Дата рождения", ""))),
                birth_place=row.get("birth_place", row.get("Место рождения", "")),
                snils=row.get("snils", row.get("СНИЛС", "")),
                inn=row.get("inn", row.get("ИНН", "")),
                inclusion_reason=row.get("inclusion_reason", row.get("Причина включения", "")),
                inclusion_date=_parse_date(
                    row.get("inclusion_date", row.get("Дата включения", ""))
                ),
                status=RosfinmonitoringEntryStatus.ACTIVE,
                raw_data=row,
            )
            entries.append(entry)

        return entries


class JsonRosfinmonitoringParser:
    """Parser for JSON format Rosfinmonitoring data."""

    def parse(self, raw_content: bytes) -> list[RosfinmonitoringEntry]:
        """Parse JSON content into RosfinmonitoringEntry objects."""
        data = json.loads(raw_content.decode("utf-8"))

        if isinstance(data, dict):
            data = data.get("entries", data.get("persons", []))

        entries: list[RosfinmonitoringEntry] = []

        for item in data:
            if not isinstance(item, dict):
                continue

            full_name = item.get("full_name", item.get("ФИО", item.get("name", "")))
            if not full_name:
                continue

            normalized_name = _normalize_name(full_name)
            matching_key = _create_matching_key(normalized_name)

            entry = RosfinmonitoringEntry(
                snapshot_id=0,
                full_name=full_name.strip(),
                normalized_name=normalized_name,
                matching_key=matching_key,
                birth_date=_parse_date(item.get("birth_date", item.get("Дата рождения", ""))),
                birth_place=item.get("birth_place", item.get("Место рождения", "")),
                snils=item.get("snils", item.get("СНИЛС", "")),
                inn=item.get("inn", item.get("ИНН", "")),
                inclusion_reason=item.get("inclusion_reason", item.get("Причина включения", "")),
                inclusion_date=_parse_date(
                    item.get("inclusion_date", item.get("Дата включения", ""))
                ),
                status=RosfinmonitoringEntryStatus.ACTIVE,
                raw_data=item,
            )
            entries.append(entry)

        return entries


class XmlRosfinmonitoringParser:
    """Parser for XML format Rosfinmonitoring data."""

    def parse(self, raw_content: bytes) -> list[RosfinmonitoringEntry]:
        """Parse XML content into RosfinmonitoringEntry objects."""
        import xml.etree.ElementTree as ET

        root = ET.fromstring(raw_content.decode("utf-8"))
        entries: list[RosfinmonitoringEntry] = []

        for person_elem in root.findall(".//person") or root.findall(".//entry"):
            full_name = (
                person_elem.findtext("full_name")
                or person_elem.findtext("ФИО")
                or person_elem.findtext("name")
                or ""
            )

            if not full_name:
                continue

            normalized_name = _normalize_name(full_name)
            matching_key = _create_matching_key(normalized_name)

            raw_data: dict[str, Any] = {}
            for child in person_elem:
                raw_data[child.tag] = child.text or ""

            entry = RosfinmonitoringEntry(
                snapshot_id=0,
                full_name=full_name.strip(),
                normalized_name=normalized_name,
                matching_key=matching_key,
                birth_date=_parse_date(
                    person_elem.findtext("birth_date") or person_elem.findtext("Дата рождения")
                ),
                birth_place=(
                    person_elem.findtext("birth_place") or person_elem.findtext("Место рождения")
                ),
                snils=person_elem.findtext("snils") or person_elem.findtext("СНИЛС"),
                inn=person_elem.findtext("inn") or person_elem.findtext("ИНН"),
                inclusion_reason=(
                    person_elem.findtext("inclusion_reason")
                    or person_elem.findtext("Причина включения")
                ),
                inclusion_date=_parse_date(
                    person_elem.findtext("inclusion_date") or person_elem.findtext("Дата включения")
                ),
                status=RosfinmonitoringEntryStatus.ACTIVE,
                raw_data=raw_data,
            )
            entries.append(entry)

        return entries
