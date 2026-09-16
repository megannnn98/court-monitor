from datetime import datetime
from pathlib import Path

import pytest

from sources.ingestion_errors import ParseError
from sources.models import RawDocument
from sources.sudrf.article_parser import SUDRF_TIMEZONE, SudrfArticleParser

FIXTURE_364 = Path("tests/fixtures/sudrf_article_364.html")
FIXTURE_369 = Path("tests/fixtures/sudrf_article_369.html")


def _raw(path: Path, external_id: str) -> RawDocument:
    return RawDocument(
        external_id=external_id,
        url=f"https://2zovs.msk.sudrf.ru/modules.php?name=press_dep&op=1&did={external_id}",
        fetched_at=datetime(2026, 9, 16, tzinfo=SUDRF_TIMEZONE),
        content_type="text/html; charset=windows-1251",
        content=path.read_bytes(),
    )


def test_parse_reads_title_date_and_body() -> None:
    article = SudrfArticleParser().parse(_raw(FIXTURE_369, "369"))

    assert article.external_id == "369"
    assert article.title == (
        "Житель Московской области осужден за приготовление к совершению теракта в Москве"
    )
    assert article.published_at == datetime(2026, 8, 7, tzinfo=SUDRF_TIMEZONE)
    assert "Кирилла Филасова" in article.text
    assert "ч. 1 ст. 30, п. «а» ч. 2 ст. 205" in article.text
    assert "лишения свободы на срок 6 лет" in article.text


def test_parse_decodes_the_windows_1251_pages_the_court_serves() -> None:
    """The pages carry no UTF-8; a wrong decode turns every Russian name into mojibake."""
    article = SudrfArticleParser().parse(_raw(FIXTURE_364, "364"))

    assert "Дмитрия Бочкова" in article.text
    assert "�" not in article.text
    assert "�" not in article.title


def test_parse_keeps_paragraph_breaks_and_drops_blank_paragraphs() -> None:
    article = SudrfArticleParser().parse(_raw(FIXTURE_364, "364"))

    paragraphs = article.text.split("\n\n")

    assert len(paragraphs) > 1
    assert all(paragraph.strip() for paragraph in paragraphs)


def test_parse_rejects_a_page_without_a_title() -> None:
    raw = _raw(FIXTURE_364, "364")
    raw = raw.model_copy(update={"content": b"<html><body><p>text</p></body></html>"})

    with pytest.raises(ParseError, match="Title not found"):
        SudrfArticleParser().parse(raw)


def test_parse_rejects_a_page_without_a_body() -> None:
    raw = _raw(FIXTURE_364, "364")
    raw = raw.model_copy(
        update={"content": b"<table><tr><td id='tdNewsDetailedTitle'>Title</td></tr></table>"},
    )

    with pytest.raises(ParseError, match="Paragraphs not found"):
        SudrfArticleParser().parse(raw)


def test_parse_leaves_published_at_unset_when_the_date_line_is_missing() -> None:
    raw = _raw(FIXTURE_364, "364")
    raw = raw.model_copy(
        update={
            "content": (
                b"<table><tr>"
                b"<td id='tdNewsDetailedTitle'>Title</td>"
                b"<td class='printVersionBody'><p>text</p></td>"
                b"</tr></table>"
            )
        },
    )

    assert SudrfArticleParser().parse(raw).published_at is None


def test_parse_drops_the_headline_the_body_repeats_as_its_first_paragraph() -> None:
    """Keeping it made the event extractor read every verdict twice (59 of 250 events)."""
    article = SudrfArticleParser().parse(_raw(FIXTURE_369, "369"))

    assert article.text.split("\n\n")[0] != article.title
    assert article.text.startswith("2-й Западный окружной военный суд рассмотрел уголовное дело")
    assert article.title not in article.text


def test_parse_keeps_a_first_paragraph_that_merely_resembles_the_headline() -> None:
    raw = _raw(FIXTURE_364, "364")
    raw = raw.model_copy(
        update={
            "content": (
                "<table><tr>"
                "<td id='tdNewsDetailedTitle'>Студент оштрафован</td>"
                "<td class='printVersionBody'>"
                "<p>Студент оштрафован судом</p><p>Подробности дела</p>"
                "</td></tr></table>"
            ).encode()
        },
    )

    article = SudrfArticleParser().parse(raw)

    assert article.text == "Студент оштрафован судом\n\nПодробности дела"


def test_parse_rejects_a_body_that_holds_nothing_but_the_headline() -> None:
    raw = _raw(FIXTURE_364, "364")
    raw = raw.model_copy(
        update={
            "content": (
                "<table><tr>"
                "<td id='tdNewsDetailedTitle'>Студент оштрафован</td>"
                "<td class='printVersionBody'><p>Студент оштрафован</p></td>"
                "</tr></table>"
            ).encode()
        },
    )

    with pytest.raises(ParseError, match="Paragraphs not found"):
        SudrfArticleParser().parse(raw)
