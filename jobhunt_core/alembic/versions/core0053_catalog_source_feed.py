"""Index source-filtered catalog pages by their primary incarnation.

The global keyset index is ideal for an unfiltered feed. For a sparse source it
walked the whole active corpus and performed one primary-incarnation lookup per
vacancy before finding the requested rows. The source-first query in api/v1.py
uses this partial index to join only the matching primary incarnations.

Revision ID: core0053
Revises: core0052
"""

from typing import Sequence, Union

from alembic import op

from jobhunt_core.config import settings

revision: str = "core0053"
down_revision: Union[str, None] = "core0052"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = settings.CORE_DB_SCHEMA


def upgrade() -> None:
    op.execute(
        f"CREATE INDEX ix_vacancies_primary_source_feed ON {S}.vacancies "
        f"(primary_incarnation_id, created_at DESC, id DESC) "
        f"WHERE archived_at IS NULL AND merged_into IS NULL"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX {S}.ix_vacancies_primary_source_feed")
