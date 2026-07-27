"""Adapter for Rosfinmonitoring (fedsfm.ru) terrorist/extremist registry.

Supports CSV fixture mode (tests) and live HTTP mode (DBF/XML download).
The public list is published at https://fedsfm.ru/documents/terrorists-catalog-portal-act
in DBF and XML formats. For MVP, we parse a CSV export as fixture.
"""

from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from court_monitor.normalization import normalize_fio

ADAPTER_VERSION = "rfm-0.1"
FIXTURE_DIR = Path("tests/fixtures/rfm")


@dataclass
class PersonRow:
    """A single parsed row from the Rosfinmonitoring list."""

    raw_name: str
    normalized_name: str
    normalization_confidence: float
    birth_date: str | None
    birth_place: str | None
    category: str | None
    source_ref: str | None
    added_date: str | None
    raw_line: str

    @property
    def dedup_key(self) -> str:
        parts = [self.normalized_name, self.birth_date or ""]
        return hashlib.sha256("|".join(parts).encode()).hexdigest()


def parse_rfm_csv(path: Path) -> list[PersonRow]:
    """Parse a CSV file with Rosfinmonitoring data.

    Expected columns: Номер п/п, ФИО, Дата рождения, Место рождения,
    Основание включения, Дата включения.
    """
    rows: list[PersonRow] = []
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter=",")
        for line in reader:
            raw_name = (line.get("ФИО") or "").strip()
            if not raw_name:
                continue
            norm, conf = _normalize_name(raw_name)
            birth_raw = (line.get("Дата рождения") or "").strip() or None
            birth_date = _normalize_date(birth_raw) if birth_raw else None
            rows.append(
                PersonRow(
                    raw_name=raw_name,
                    normalized_name=norm,
                    normalization_confidence=conf,
                    birth_date=birth_date,
                    birth_place=(line.get("Место рождения") or "").strip() or None,
                    category=(line.get("Основание включения") or "").strip() or None,
                    source_ref=(line.get("Номер п/п") or "").strip() or None,
                    added_date=(line.get("Дата включения") or "").strip() or None,
                    raw_line=",".join(str(v) for v in line.values()),
                )
            )
    return rows


def load_fixture_rows() -> list[PersonRow]:
    """Load all rows from the default fixture CSV."""
    fixture = FIXTURE_DIR / "persons.csv"
    if not fixture.exists():
        return []
    return parse_rfm_csv(fixture)


def _normalize_name(raw: str) -> tuple[str, float]:
    """Normalize a name and return (normalized, confidence).

    High confidence for 3-token names (Фамилия Имя Отчество),
    lower for 2-token or partial names.
    """
    normalized = normalize_fio(raw)
    tokens = normalized.split()
    if len(tokens) >= 3:
        return normalized, 0.95
    if len(tokens) == 2:
        return normalized, 0.70
    return normalized, 0.40


def _normalize_date(raw: str) -> str | None:
    """Normalize date to ISO format (YYYY-MM-DD).

    Handles: DD.MM.YYYY, YYYY-MM-DD.
    """
    if not raw:
        return None
    # ISO format already
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if m:
        return raw
    # DD.MM.YYYY
    m = re.match(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", raw)
    if m:
        return f"{m.group(3)}-{m.group(2).zfill(2)}-{m.group(1).zfill(2)}"
    return raw
