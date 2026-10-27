"""Shared filter choices and links used by API responses."""

from pydantic import BaseModel


class OptionResponse(BaseModel):
    """One choice of a filter: its value, the console's words and, where the list counts
    it, how many rows it would show."""

    value: str
    label: str
    count: int | None = None


class EventCountResponse(BaseModel):
    kind: str
    label: str
    count: int


class PersonLinkResponse(BaseModel):
    key: str
    # Surname first.
    name: str
    dossier_url: str


class NameFormResponse(BaseModel):
    form: str
    count: int
