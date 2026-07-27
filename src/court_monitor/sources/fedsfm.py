"""Adapter for Rosfinmonitoring (fedsfm.ru) terrorist/extremist registry.

Supports: CSV (internal fixture), XML, DBF, ZIP (containing XML or DBF).
The public list is published at https://fedsfm.ru/documents/terrorists-catalog-portal-act
in DBF and XML formats. Operator downloads the file manually and imports via --file.
"""

from __future__ import annotations

import csv
import hashlib
import re
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree

from court_monitor.normalization import normalize_fio

ADAPTER_VERSION = "rfm-0.1"
FIXTURE_DIR = Path("tests/fixtures/rfm")

# XML tag names (Russian, from Приказ РФМ №134)
_XML_FIO_TAGS = ("ФИО", "ФИОФЛ", "fio", "FIO")
_XML_BIRTH_TAGS = ("ДатаРожд", "ДатаРождФЛ", "birthday", "BIRTHDAY")
_XML_BIRTHPLACE_TAGS = ("МестоРожд", "МестоРождФЛ", "birthplace", "BIRTHPLACE")
_XML_REASON_TAGS = ("Основание", "Причина", "reason", "REASON")
_XML_DATE_ADD_TAGS = ("ДатаВключ", "ДатаДоб", "date_add", "DATE_ADD")
_XML_NUM_TAGS = ("НомерЗап", "Номер", "ПорНомер", "num", "NUM")

# DBF field names
_DBF_FIO_FIELDS = ("FIO", "fio", "FAMILY", "NAME", "F_NAME")
_DBF_BIRTH_FIELDS = ("BIRTHDAY", "birthday", "DAT_ROZH", "BIRTHDATE")
_DBF_BIRTHPLACE_FIELDS = ("BIRTHPLACE", "birthplace", "MEST_ROZH")
_DBF_REASON_FIELDS = ("REASON", "reason", "OSNOVANIE")
_DBF_DATE_ADD_FIELDS = ("DATE_ADD", "date_add", "DAT_DOBAV")
_DBF_NUM_FIELDS = ("NUM", "num", "NOMER", "N_PP")


@dataclass
class PersonRow:
    """A single parsed row from the Rosfinmonitoring list."""

    raw_name: str
    normalized_name: str
    search_name: str
    normalization_confidence: float
    normalization_method: str
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


@dataclass
class ParseResult:
    """Result of parsing an RFM file."""

    rows: list[PersonRow] = field(default_factory=list)
    format_detected: str = ""
    total_records: int = 0
    recognized: int = 0
    unrecognized: int = 0
    errors: list[str] = field(default_factory=list)


def parse_file(path: Path) -> ParseResult:
    """Parse an RFM file (auto-detect format: XML, DBF, ZIP, CSV)."""
    suffix = path.suffix.lower()

    if suffix == ".zip":
        return _parse_zip(path)
    if suffix == ".xml":
        return _parse_xml(path)
    if suffix == ".dbf":
        return _parse_dbf(path)
    if suffix == ".csv":
        rows = parse_rfm_csv(path)
        return ParseResult(
            rows=rows,
            format_detected="csv",
            total_records=len(rows),
            recognized=len(rows),
        )
    # Try to detect by content
    return _parse_unknown(path)


def parse_rfm_csv(path: Path) -> list[PersonRow]:
    """Parse a CSV file with Rosfinmonitoring data."""
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
                    search_name=_search_name(raw_name),
                    normalization_confidence=conf,
                    normalization_method="lowercase",
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


def _parse_xml(path: Path) -> ParseResult:
    """Parse an XML file with RFM data."""
    result = ParseResult(format_detected="xml")
    try:
        tree = ElementTree.parse(path)
        root = tree.getroot()
    except ElementTree.ParseError as e:
        result.errors.append(f"XML parse error: {e}")
        return result

    # Find all person elements (try common tag patterns)
    person_elements = _find_person_elements(root)
    result.total_records = len(person_elements)

    for elem in person_elements:
        try:
            row = _xml_element_to_row(elem)
            if row:
                result.rows.append(row)
                result.recognized += 1
            else:
                result.unrecognized += 1
                result.errors.append(f"Could not parse element: {elem.tag}")
        except Exception as e:
            result.unrecognized += 1
            result.errors.append(f"Error parsing element: {e}")

    return result


def _find_person_elements(root: ElementTree.Element) -> list[ElementTree.Element]:
    """Find person elements in XML (try various tag patterns)."""
    # Common patterns: СвФЛ, ФизЛицо, Person, RECORD, Запись
    patterns = ["СвФЛ", "ФизЛицо", "Person", "RECORD", "Запись", "ФЛ"]
    for pattern in patterns:
        elements = root.findall(f".//{pattern}")
        if elements:
            return elements
    # Fallback: look for elements that have FIO-like children
    for elem in root.iter():
        for fio_tag in _XML_FIO_TAGS:
            if elem.find(fio_tag) is not None:
                return [elem]
    return []


def _xml_element_to_row(elem: ElementTree.Element) -> PersonRow | None:
    """Convert an XML element to a PersonRow."""
    raw_name = _find_text(elem, _XML_FIO_TAGS)
    if not raw_name:
        return None

    birth_raw = _find_text(elem, _XML_BIRTH_TAGS)
    birth_date = _normalize_date(birth_raw) if birth_raw else None

    norm, conf = _normalize_name(raw_name)
    return PersonRow(
        raw_name=raw_name,
        normalized_name=norm,
        search_name=_search_name(raw_name),
        normalization_confidence=conf,
        normalization_method="lowercase",
        birth_date=birth_date,
        birth_place=_find_text(elem, _XML_BIRTHPLACE_TAGS),
        category=_find_text(elem, _XML_REASON_TAGS),
        source_ref=_find_text(elem, _XML_NUM_TAGS),
        added_date=_find_text(elem, _XML_DATE_ADD_TAGS),
        raw_line=ElementTree.tostring(elem, encoding="unicode"),
    )


def _find_text(elem: ElementTree.Element, tags: tuple[str, ...]) -> str | None:
    """Find text content in element or its children by trying multiple tag names."""
    for tag in tags:
        found = elem.find(f".//{tag}")
        if found is not None and found.text:
            return found.text.strip()
    return None


def _parse_dbf(path: Path) -> ParseResult:
    """Parse a DBF file with RFM data."""
    result = ParseResult(format_detected="dbf")
    try:
        from dbfread import DBF  # noqa: PLC0415
    except ImportError:
        result.errors.append("dbfread not installed")
        return result

    try:
        # Try different encodings
        for encoding in ("cp866", "utf-8", "cp1251"):
            try:
                db = DBF(str(path), encoding=encoding, load=True)
                result.total_records = len(db)
                for record in db:
                    try:
                        row = _dbf_record_to_row(record)
                        if row:
                            result.rows.append(row)
                            result.recognized += 1
                        else:
                            result.unrecognized += 1
                    except Exception as e:
                        result.unrecognized += 1
                        result.errors.append(f"DBF record error: {e}")
                break
            except Exception:
                continue
        else:
            result.errors.append("Could not read DBF with any encoding")
    except Exception as e:
        result.errors.append(f"DBF error: {e}")

    return result


def _dbf_record_to_row(record: dict) -> PersonRow | None:
    """Convert a DBF record to a PersonRow."""
    raw_name = _find_dbf_field(record, _DBF_FIO_FIELDS)
    if not raw_name:
        return None

    birth_raw = _find_dbf_field(record, _DBF_BIRTH_FIELDS)
    birth_date = _normalize_date(birth_raw) if birth_raw else None

    norm, conf = _normalize_name(raw_name)
    return PersonRow(
        raw_name=raw_name,
        normalized_name=norm,
        search_name=_search_name(raw_name),
        normalization_confidence=conf,
        normalization_method="lowercase",
        birth_date=birth_date,
        birth_place=_find_dbf_field(record, _DBF_BIRTHPLACE_FIELDS),
        category=_find_dbf_field(record, _DBF_REASON_FIELDS),
        source_ref=_find_dbf_field(record, _DBF_NUM_FIELDS),
        added_date=_find_dbf_field(record, _DBF_DATE_ADD_FIELDS),
        raw_line=str(record),
    )


def _find_dbf_field(record: dict, field_names: tuple[str, ...]) -> str | None:
    """Find a field value in a DBF record by trying multiple field names."""
    for name in field_names:
        val = record.get(name)
        if val is not None:
            return str(val).strip()
    return None


def _parse_zip(path: Path) -> ParseResult:
    """Parse a ZIP file containing XML or DBF."""
    result = ParseResult(format_detected="zip")
    try:
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                lower = name.lower()
                if lower.endswith(".xml") or lower.endswith(".dbf"):
                    with tempfile.TemporaryDirectory() as tmpdir:
                        extracted = zf.extract(name, tmpdir)
                        sub_result = parse_file(Path(extracted))
                        result.rows.extend(sub_result.rows)
                        result.total_records += sub_result.total_records
                        result.recognized += sub_result.recognized
                        result.unrecognized += sub_result.unrecognized
                        result.errors.extend(sub_result.errors)
                        result.format_detected = f"zip→{sub_result.format_detected}"
                    break
            else:
                # ZIP might be a renamed XML (common for UN list EN)
                result.errors.append("No XML or DBF found in ZIP archive")
    except zipfile.BadZipFile:
        # Try as XML (some ZIP files are actually XML renamed)
        result = _parse_xml(path)
        result.format_detected = "xml (was .zip)"
    except Exception as e:
        result.errors.append(f"ZIP error: {e}")

    return result


def _parse_unknown(path: Path) -> ParseResult:
    """Try to detect format by content."""
    result = ParseResult(format_detected="unknown")
    try:
        content = path.read_bytes()
        # Check if XML
        if content.startswith(b"<?xml") or content.startswith(b"<"):
            return _parse_xml(path)
        # Check if DBF (magic byte)
        if len(content) > 4 and content[0] in (0x03, 0x83, 0x30):
            return _parse_dbf(path)
        result.errors.append("Could not detect file format")
    except Exception as e:
        result.errors.append(f"Error reading file: {e}")
    return result


def _normalize_name(raw: str) -> tuple[str, float]:
    """Normalize a name and return (normalized, confidence)."""
    normalized = normalize_fio(raw)
    tokens = normalized.split()
    if len(tokens) >= 3:
        return normalized, 0.95
    if len(tokens) == 2:
        return normalized, 0.70
    return normalized, 0.40


def _search_name(raw: str) -> str:
    """Create a lowercase search key from a name."""
    return raw.lower().strip()


def _normalize_date(raw: str) -> str | None:
    """Normalize date to ISO format (YYYY-MM-DD)."""
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
    # DBF date format (YYYYMMDD)
    m = re.match(r"(\d{4})(\d{2})(\d{2})", raw)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return raw
