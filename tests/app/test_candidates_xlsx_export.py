"""Excel export of the Candidates page (`/ui/candidates/export.xlsx`)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from io import BytesIO

from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy.orm import Session, sessionmaker
from support.research_db_fixtures import ResearchSeeder

from api import app, get_db

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
HEADER = ("№", "Имя человека", "Ссылка")


@contextmanager
def _client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    def override_get_db() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def _seed_candidate(
    seed: ResearchSeeder,
    snapshot_id: int,
    name: str,
    *,
    confidence: float = 0.9,
    rf_status: str = "not_matched",
) -> int:
    person_id = seed.person(name)
    seed.classification(person_id, "political", confidence)
    seed.match(person_id, snapshot_id, rf_status, 0.8)
    return person_id


def _rows(content: bytes) -> list[tuple[object, ...]]:
    sheet = load_workbook(BytesIO(content)).active
    assert sheet is not None
    return list(sheet.iter_rows(values_only=True))


def _person_url(person_id: int) -> str:
    return f"http://testserver/ui/persons/{person_id}"


def test_export_returns_downloadable_xlsx_with_candidate_row(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        seed = ResearchSeeder(session)
        snapshot_id = seed.snapshot()
        person_id = _seed_candidate(seed, snapshot_id, "Иван Иванов")
        session.commit()

    with _client(session_factory) as client:
        response = client.get("/ui/candidates/export.xlsx", params={"snapshot_id": snapshot_id})

    assert response.status_code == 200
    assert response.headers["content-type"] == XLSX_MEDIA_TYPE
    assert response.headers["content-disposition"] == (
        f'attachment; filename="political-candidates-{snapshot_id}.xlsx"'
    )
    sheet = load_workbook(BytesIO(response.content)).active
    assert sheet is not None
    assert sheet.max_column == 3
    assert list(sheet.iter_rows(values_only=True)) == [
        HEADER,
        (1, "Иван Иванов", _person_url(person_id)),
    ]
    hyperlink = sheet.cell(row=2, column=3).hyperlink
    assert hyperlink is not None
    assert hyperlink.target == _person_url(person_id)


def test_export_keeps_service_order_for_several_candidates(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        seed = ResearchSeeder(session)
        snapshot_id = seed.snapshot()
        # Names are not alphabetical: the order must be the service's (person id).
        names = ["Яков Яковлев", "Анна Смирнова", "Пётр Петров"]
        ids = [_seed_candidate(seed, snapshot_id, name) for name in names]
        session.commit()

    with _client(session_factory) as client:
        response = client.get("/ui/candidates/export.xlsx", params={"snapshot_id": snapshot_id})

    assert _rows(response.content) == [
        HEADER,
        *[
            (position, name, _person_url(person_id))
            for position, (name, person_id) in enumerate(zip(names, ids, strict=True), start=1)
        ],
    ]


def test_export_applies_active_filters(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        seed = ResearchSeeder(session)
        snapshot_id = seed.snapshot()
        other_snapshot_id = seed.snapshot("other-snapshot-hash")
        strong = _seed_candidate(seed, snapshot_id, "Иван Иванов", confidence=0.95)
        _seed_candidate(seed, snapshot_id, "Слабый Кандидат", confidence=0.75)
        _seed_candidate(seed, snapshot_id, "Есть Вперечне", rf_status="matched")
        _seed_candidate(seed, other_snapshot_id, "Другой Снапшот")
        session.commit()

    with _client(session_factory) as client:
        response = client.get(
            "/ui/candidates/export.xlsx",
            params={"snapshot_id": snapshot_id, "min_confidence": 0.9},
        )

    assert _rows(response.content) == [HEADER, (1, "Иван Иванов", _person_url(strong))]


def test_export_contains_all_candidates_not_only_the_page_limit(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        seed = ResearchSeeder(session)
        snapshot_id = seed.snapshot()
        names = ["Первый Кандидат", "Второй Кандидат", "Третий Кандидат"]
        for name in names:
            _seed_candidate(seed, snapshot_id, name)
        session.commit()

    params = {"snapshot_id": snapshot_id, "limit": 2}
    with _client(session_factory) as client:
        page = client.get("/ui/candidates", params=params)
        response = client.get("/ui/candidates/export.xlsx", params=params)

    assert "Третий Кандидат" not in page.text
    assert [row[1] for row in _rows(response.content)[1:]] == names


def test_export_without_candidates_has_only_header(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        snapshot_id = ResearchSeeder(session).snapshot()
        session.commit()

    with _client(session_factory) as client:
        response = client.get("/ui/candidates/export.xlsx", params={"snapshot_id": snapshot_id})

    assert response.status_code == 200
    assert _rows(response.content) == [HEADER]


def test_export_of_unknown_snapshot_is_not_found(
    session_factory: sessionmaker[Session],
) -> None:
    with _client(session_factory) as client:
        response = client.get("/ui/candidates/export.xlsx", params={"snapshot_id": 999})

    assert response.status_code == 404


def test_candidates_page_links_export_with_active_filters(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        snapshot_id = ResearchSeeder(session).snapshot()
        session.commit()

    with _client(session_factory) as client:
        page = client.get(
            "/ui/candidates", params={"snapshot_id": snapshot_id, "min_confidence": 0.8}
        )

    assert (
        f'href="/ui/candidates/export.xlsx?snapshot_id={snapshot_id}&min_confidence=0.8"'
        in page.text
    )
    assert ">Export to Excel</a>" in page.text


def test_export_writes_formula_like_name_as_text(
    session_factory: sessionmaker[Session],
) -> None:
    name = '=HYPERLINK("https://evil.test","Иван")'
    with session_factory() as session:
        seed = ResearchSeeder(session)
        snapshot_id = seed.snapshot()
        _seed_candidate(seed, snapshot_id, name)
        session.commit()

    with _client(session_factory) as client:
        response = client.get("/ui/candidates/export.xlsx", params={"snapshot_id": snapshot_id})

    sheet = load_workbook(BytesIO(response.content)).active
    assert sheet is not None
    name_cell = sheet.cell(row=2, column=2)
    assert name_cell.value == name
    assert name_cell.data_type == "s"
