"""`column = ANY(:ids)`: one bound array however many ids, where `IN (...)` would bind one
parameter per id (PostgreSQL takes at most 65535)."""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

from sqlalchemy import ARRAY, ColumnElement, Integer, String, bindparam, func


def any_of(column: Any, ids: Collection[int], *, name: str = "ids") -> ColumnElement[bool]:
    """`column = ANY(:name)` over integer ids, sorted and without repeats."""
    return column == func.any(bindparam(name, sorted(set(ids)), type_=ARRAY(Integer)))  # type: ignore[no-any-return]


def any_text(column: Any, values: Collection[str], *, name: str = "values") -> ColumnElement[bool]:
    """`column = ANY(:name)` over strings, sorted and without repeats."""
    return column == func.any(bindparam(name, sorted(set(values)), type_=ARRAY(String)))  # type: ignore[no-any-return]
