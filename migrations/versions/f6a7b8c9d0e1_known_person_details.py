"""What the operator's base says of a known person beside the name

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-10-02 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: str | Sequence[str] | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS: tuple[tuple[str, sa.types.TypeEngine[object]], ...] = (
    ("gender", sa.String(length=16)),
    ("birth_date", sa.Date()),
    ("region", sa.Text()),
    ("city", sa.Text()),
    ("articles", sa.Text()),
    ("case_opened_on", sa.Date()),
    ("sentenced_on", sa.Date()),
    ("court", sa.Text()),
    ("court_card_url", sa.Text()),
    ("in_rfm", sa.Boolean()),
    ("rfm_included_on", sa.Date()),
)


def upgrade() -> None:
    """All nullable: the rows already here stay as they are until the next sync fills them."""
    for name, kind in _COLUMNS:
        op.add_column("airtable_known_persons", sa.Column(name, kind, nullable=True))


def downgrade() -> None:
    for name, _ in reversed(_COLUMNS):
        op.drop_column("airtable_known_persons", name)
