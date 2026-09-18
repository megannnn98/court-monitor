"""The candidates exports as files: CSV, Excel and PDF, built from rows, not requests.

The routes in `web.ui.candidates` choose the rows and wrap the bytes in a response.
"""

from collections.abc import Sequence
from csv import writer
from html import escape
from io import BytesIO, StringIO
from pathlib import Path

from openpyxl import Workbook
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from web.candidate_rows import _CANDIDATE_CATEGORIES, _CandidateRow, _news_day, _surname_first
from web.response_models import CandidateResponse


class PdfFontMissingError(RuntimeError):
    """The Cyrillic font the PDF needs is not installed."""


def candidates_csv(candidates: Sequence[CandidateResponse]) -> str:
    """UTF-8 with a BOM, so Excel opens the Cyrillic right."""
    output = StringIO(newline="")
    csv_writer = writer(output)
    csv_writer.writerow(
        [
            "person_id",
            "canonical_name",
            "persecution_confidence",
            "persecution_reasons",
            "event_count",
            "rosfinmonitoring_status",
            "rosfinmonitoring_match_confidence",
        ]
    )
    for candidate in candidates:
        csv_writer.writerow(
            [
                candidate.person_id,
                candidate.canonical_name,
                candidate.persecution_confidence,
                "; ".join(candidate.persecution_reasons),
                candidate.event_count,
                candidate.rosfinmonitoring_status,
                candidate.rosfinmonitoring_match_confidence,
            ]
        )
    return "\ufeff" + output.getvalue()


def candidates_xlsx(rows: Sequence[_CandidateRow]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Кандидаты"
    sheet.append(["№", "Фамилия Имя", "Дата новости", "Категория", "Причины", "Ссылка"])
    for position, (candidate, news) in enumerate(rows, start=1):
        link = news.url if news is not None else None
        published = _news_day(news.published_at) if news is not None and news.published_at else None
        category = (
            _CANDIDATE_CATEGORIES.get(news.event_type, news.event_type)
            if news is not None and news.event_type
            else None
        )
        # The same «Причины» as the PDF export.
        reasons = "; ".join(candidate.persecution_reasons) or None
        sheet.append(
            [position, _surname_first(candidate.canonical_name), published, category, reasons, link]
        )
        row = position + 1
        # Names and URLs come from scraped sources: never let a leading "=" become a formula.
        for column, value in ((2, True), (5, reasons), (6, link)):
            if value is not None:
                sheet.cell(row=row, column=column).data_type = "s"
        if published is not None:
            sheet.cell(row=row, column=3).number_format = "DD.MM.YYYY"
        # Only web links are clickable: a scraped «javascript:» URL stays plain text.
        if link is not None and link.startswith(("http://", "https://")):
            sheet.cell(row=row, column=6).hyperlink = link
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _candidate_pdf_font() -> tuple[str, str]:
    regular_paths = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
    )
    bold_paths = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    )
    regular = next((Path(path) for path in regular_paths if Path(path).exists()), None)
    bold = next((Path(path) for path in bold_paths if Path(path).exists()), None)
    if regular is None or bold is None:
        raise PdfFontMissingError("Cyrillic PDF font is not installed")
    if "CandidateSans" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("CandidateSans", str(regular)))
    if "CandidateSans-Bold" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("CandidateSans-Bold", str(bold)))
    return "CandidateSans", "CandidateSans-Bold"


def candidates_pdf(
    candidates: Sequence[CandidateResponse], *, snapshot_id: int, min_confidence: float
) -> bytes:
    regular_font, bold_font = _candidate_pdf_font()
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=10 * mm,
        leftMargin=10 * mm,
        topMargin=10 * mm,
        bottomMargin=10 * mm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "CandidatePdfTitle", parent=styles["Title"], fontName=bold_font, fontSize=14, leading=18
    )
    cell_style = ParagraphStyle(
        "CandidatePdfCell", parent=styles["BodyText"], fontName=regular_font, fontSize=7, leading=9
    )
    header_style = ParagraphStyle(
        "CandidatePdfHeader", parent=cell_style, fontName=bold_font, textColor=colors.white
    )

    def cell(value: object, *, header: bool = False) -> Paragraph:
        return Paragraph(escape(str(value)), header_style if header else cell_style)

    headers = [
        "№",
        "ID",
        "Персона",
        "Political confidence",
        "Причины",
        "Events",
        "RF status",
        "RF match",
    ]
    data = [[cell(header, header=True) for header in headers]]
    data.extend(
        [
            cell(position),
            cell(candidate.person_id),
            cell(candidate.canonical_name),
            cell(f"{candidate.persecution_confidence:.2f}"),
            cell("; ".join(candidate.persecution_reasons)),
            cell(candidate.event_count),
            cell(candidate.rosfinmonitoring_status),
            cell(candidate.rosfinmonitoring_match_confidence or "—"),
        ]
        for position, candidate in enumerate(candidates, start=1)
    )
    table = Table(
        data,
        repeatRows=1,
        colWidths=[10 * mm, 18 * mm, 42 * mm, 25 * mm, 78 * mm, 15 * mm, 25 * mm, 22 * mm],
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#243447")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#b7c2cc")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#eef3f7")]),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story = [
        Paragraph("Политические кандидаты вне списка Росфинмониторинга", title_style),
        Paragraph(
            f"Snapshot: {snapshot_id}; minimum confidence: {min_confidence:.2f}; найдено: {len(candidates)}",
            cell_style,
        ),
        Spacer(1, 6 * mm),
        table,
    ]
    document.build(story)
    return buffer.getvalue()
