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
    <li>4. АБАКАРОВ ЗАЛИМХАН ПАХРУДИНОВИЧ*, (АБАКАРОВ ЗЕЛИМХАН ПАХРУДИНОВИЧ), 26.04.1981 г.р. , Г. ХАСАВЮРТ ДАССР;</li>
    <li>5. АБДУКАРИМОВ МАГОМЕД МАГОМЕДОВИЧ*, (АБДУКЕРИМОВ МАГОМЕД МАГОМЕДОВИЧ; АБДУЛКЕРИМОВ МАГОМЕД МАГОМЕДОВИЧ), 03.05.1964 г.р. , С. ЭЧЕДА УСМАДИНСКОГО РАЙОНА;</li>
    <li>6. ИСЛОМОВ ЗАКРУЛЛО ИСМАТУЛЛОЕВИЧ, , РЕСПУБЛИКА ТАДЖИКИСТАН;</li>
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
        "АБАКАРОВ ЗАЛИМХАН ПАХРУДИНОВИЧ",
        "АБДУКАРИМОВ МАГОМЕД МАГОМЕДОВИЧ",
        "ИСЛОМОВ ЗАКРУЛЛО ИСМАТУЛЛОЕВИЧ",
    ]
    listed = entries[0]
    assert listed.birth_date == datetime(1996, 6, 8, tzinfo=UTC)
    assert listed.birth_place == "П. МАМЕДКАЛА"
    assert listed.normalized_name == "абабакаров абдулла гасанович"
    assert listed.matching_key == "абабакаровабдуллагасанович"
    assert listed.raw_data["entry"].startswith("1. АБАБАКАРОВ")


def test_an_entry_without_a_birth_date_or_place_is_still_listed() -> None:
    entries = HtmlRosfinmonitoringParser().parse(PAGE)

    without_details = next(e for e in entries if e.full_name == "ИВАНОВ ИВАН ИВАНОВИЧ")
    assert without_details.birth_date is None
    assert without_details.birth_place is None


def test_former_names_in_brackets_do_not_eat_the_birth_date() -> None:
    """828 of the 23 023 published entries carry former names in brackets.

    A name pattern that stops at the first comma read those brackets as the name and
    left the birth date inside the birth place, so the entry lost the one field that
    identifies a person beyond the name.
    """
    entries = HtmlRosfinmonitoringParser().parse(PAGE)

    entry = next(e for e in entries if e.full_name == "АБАКАРОВ ЗАЛИМХАН ПАХРУДИНОВИЧ")
    assert entry.birth_date == datetime(1981, 4, 26, tzinfo=UTC), (
        "the birth date is published after the brackets and must be read from there"
    )
    assert entry.birth_place == "Г. ХАСАВЮРТ ДАССР", (
        "the birth place is what follows the birth date, not the brackets"
    )


def test_former_names_are_kept_and_the_published_name_stays_the_only_name() -> None:
    """The brackets are the list's record of other names; the entry is still listed under
    the published one. Nothing reads the former names yet, so they are stored and not
    folded into the name the matcher compares on.
    """
    entries = HtmlRosfinmonitoringParser().parse(PAGE)

    entry = next(e for e in entries if e.full_name == "АБДУКАРИМОВ МАГОМЕД МАГОМЕДОВИЧ")
    assert entry.raw_data["former_names"] == [
        "АБДУКЕРИМОВ МАГОМЕД МАГОМЕДОВИЧ",
        "АБДУЛКЕРИМОВ МАГОМЕД МАГОМЕДОВИЧ",
    ]
    assert entry.matching_key == "абдукаримовмагомедмагомедович", (
        "a former name must not become part of the key the matcher looks a person up by"
    )
    assert "АБДУКЕРИМОВ" not in entry.full_name


def test_an_entry_with_brackets_but_no_birth_date_keeps_them() -> None:
    """The page publishes entries with brackets and an empty date field; the brackets are
    still read rather than being taken for a name.
    """
    page = """
    <div class="panel-heading"><h4>Физические лица</h4></div>
    <div class="panel-body"><ol>
      <li>97. АБДУКАРИМОВ ХАБИБ МАГОМЕДОВИЧ*, (АБДУЛКЕРИМОВ ХАБИБ МАГОМЕДОВИЧ), , С. ЭЧЕДА;</li>
    </ol></div>
    """.encode()

    (entry,) = HtmlRosfinmonitoringParser().parse(page)

    assert entry.full_name == "АБДУКАРИМОВ ХАБИБ МАГОМЕДОВИЧ"
    assert entry.birth_date is None
    assert entry.birth_place == "С. ЭЧЕДА"
    assert entry.raw_data["former_names"] == ["АБДУЛКЕРИМОВ ХАБИБ МАГОМЕДОВИЧ"]


def test_an_entry_without_brackets_carries_no_former_names() -> None:
    (entry,) = HtmlRosfinmonitoringParser().parse(PAGE)[:1]

    assert "former_names" not in entry.raw_data, (
        "an empty key would read as a former name the list never published"
    )


def test_organisations_section_is_never_parsed_as_a_person() -> None:
    entries = HtmlRosfinmonitoringParser().parse(PAGE)

    assert all("FREE RUSSIA" not in entry.full_name for entry in entries)


def test_published_page_is_detected_as_html_not_xml() -> None:
    pipeline = RosfinmonitoringIngestionPipeline(persistence=None)  # type: ignore[arg-type]

    parser = pipeline._detect_parser(b"<!DOCTYPE html PUBLIC>\n<html><body>" + PAGE)

    assert isinstance(parser, HtmlRosfinmonitoringParser)
