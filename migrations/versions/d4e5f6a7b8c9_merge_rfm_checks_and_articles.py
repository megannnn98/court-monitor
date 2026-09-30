"""Свести две ветки миграций, разошедшиеся после справочников

Revision ID: d4e5f6a7b8c9
Revises: a7b8c9d0e1f2, c3d4e5f6a7b8
Create Date: 2026-09-30 16:00:00.000000

"""

from collections.abc import Sequence


revision: str = "d4e5f6a7b8c9"
down_revision: str | Sequence[str] | None = ("a7b8c9d0e1f2", "c3d4e5f6a7b8")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Nothing of its own.

    Two lines of work left the same revision: the check-run table and the articles
    reference list. They touch different tables and neither depends on the other, so
    there is no order to impose and nothing to reconcile here — which is the whole
    content of this migration.

    It is not an empty file out of laziness. With two heads `alembic upgrade head`
    refuses to run at all, and the revision stamped on a database is a single value, so
    one of the two branches has to be named as merged before any database can be brought
    up to date by the tool rather than by hand.
    """


def downgrade() -> None:
    """Also nothing: this migration never changed the schema, so there is nothing to undo.

    A downgrade here would move the stamp back onto one branch, and the next `upgrade`
    would run the other branch's DDL a second time.
    """
