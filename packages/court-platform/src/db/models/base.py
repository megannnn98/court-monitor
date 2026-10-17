"""The declarative base every ORM model shares: one metadata for Alembic."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
