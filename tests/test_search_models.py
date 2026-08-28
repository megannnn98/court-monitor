import pytest
from pydantic import ValidationError

from models import SearchQuery


def test_search_query_strips_text_and_uses_default_limit() -> None:
    query = SearchQuery(
        text="  реабилитация нацизма  ",
    )
    assert query.text == "реабилитация нацизма"
    assert query.limit == 10


def test_search_query_accepts_valid_limit() -> None:
    query = SearchQuery(text="  реабилитация нацизма  ", limit=25)
    assert query.limit == 25


@pytest.mark.parametrize("text", ["", "   ", "\t\n"])
def test_blank_text_is_rejected(text: str) -> None:
    with pytest.raises(ValidationError):
        SearchQuery(text=text)


@pytest.mark.parametrize("limit", [0, -1, 101])
def test_invalid_limit_is_rejected(limit: int) -> None:
    with pytest.raises(ValidationError):
        SearchQuery(text="тест", limit=limit)
