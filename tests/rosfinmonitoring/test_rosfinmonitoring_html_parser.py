"""Parsing the published list page (fedsfm.ru «Перечень террористов и экстремистов»)."""

from datetime import UTC, datetime

from rosfinmonitoring.ingestion import RosfinmonitoringIngestionPipeline
from rosfinmonitoring.parser import HtmlRosfinmonitoringParser

PAGE = """
<div class="panel-group">
  <div class="panel-heading"><h4>Национальная часть</h4></div>
  <div class="panel-heading"><h4>Организации</h4></div>
  <div class="panel-body"><ol>
    <li>1. FREE RUSSIA FOUNDATION , ;</li>
  </ol></div>
  <div class="panel-heading"><h4>Физические лица</h4></div>
  <div class="panel-body"><ol>
    <li>1. АБАБАКАРОВ АБДУЛЛА ГАСАНОВИЧ*, 08.06.1996 г.р. , П. МАМЕДКАЛА;</li>
    <li>2. ИВАНОВ ИВАН ИВАНОВИЧ*, , ;</li>
    <li>3. АБАБАКАРОВ АБДУЛЛА ГАСАНОВИЧ*, 08.06.1996 г.р. , П. МАМЕДКАЛА;</li>
  </ol></div>
  <div class="panel-heading"><h4>Международная часть</h4></div>
  <div class="panel-heading"><h4>Физические лица</h4></div>
  <div class="panel-body">Сведения о субъектах отсутствуют</div>
</div>
""".encode()


def test_parses_listed_individuals_and_skips_organisations() -> None:
    entries = HtmlRosfinmonitoringParser().parse(PAGE)

    assert [entry.full_name for entry in entries] == [
        "АБАБАКАРОВ АБДУЛЛА ГАСАНОВИЧ",
        "ИВАНОВ ИВАН ИВАНОВИЧ",
    ]
    listed = entries[0]
    assert listed.birth_date == datetime(1996, 6, 8, tzinfo=UTC)
    assert listed.birth_place == "П. МАМЕДКАЛА"
    assert listed.normalized_name == "абабакаров абдулла гасанович"
    assert listed.matching_key == "абабакаровабдуллагасанович"
    assert listed.raw_data["entry"].startswith("1. АБАБАКАРОВ")


def test_an_entry_without_a_birth_date_or_place_is_still_listed() -> None:
    (_, without_details) = HtmlRosfinmonitoringParser().parse(PAGE)

    assert without_details.birth_date is None
    assert without_details.birth_place is None


def test_organisations_section_is_never_parsed_as_a_person() -> None:
    entries = HtmlRosfinmonitoringParser().parse(PAGE)

    assert all("FREE RUSSIA" not in entry.full_name for entry in entries)


def test_published_page_is_detected_as_html_not_xml() -> None:
    pipeline = RosfinmonitoringIngestionPipeline(persistence=None)  # type: ignore[arg-type]

    parser = pipeline._detect_parser(b"<!DOCTYPE html PUBLIC>\n<html><body>" + PAGE)

    assert isinstance(parser, HtmlRosfinmonitoringParser)
