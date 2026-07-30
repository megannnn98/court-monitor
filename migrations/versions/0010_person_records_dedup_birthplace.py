"""extend person_records dedup key with birth_place

Widens the constraint so that two namesakes with no birth date can coexist
when they come from different regions. It is deliberately looser than the
application lookup in repository.find_person_record, which only consults
birth_place when the birth date is missing: with a date present a corrected
place must update the record rather than insert a second one.

Revision ID: 0010_person_records_dedup_birthplace
Revises: 0009_jobs
Create Date: 2026-07-30
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0010_person_records_dedup_birthplace"
down_revision: str | None = "0009_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("person_records") as batch:
        batch.drop_constraint("uq_person_source_name_birth", type_="unique")
        batch.create_unique_constraint(
            "uq_person_source_name_birth_place",
            ["source", "normalized_name", "birth_date", "birth_place"],
        )


def downgrade() -> None:
    with op.batch_alter_table("person_records") as batch:
        batch.drop_constraint("uq_person_source_name_birth_place", type_="unique")
        batch.create_unique_constraint(
            "uq_person_source_name_birth",
            ["source", "normalized_name", "birth_date"],
        )
