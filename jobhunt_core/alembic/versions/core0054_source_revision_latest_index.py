"""Index the latest raw revision lookup used by source aggregates.

Revision ID: core0054
Revises: core0053
"""

from typing import Sequence, Union

from alembic import op

from jobhunt_core.config import settings

revision: str = "core0054"
down_revision: Union[str, None] = "core0053"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = settings.CORE_DB_SCHEMA


def upgrade() -> None:
    op.execute(
        f"CREATE INDEX ix_slrev_incarnation_fetched ON "
        f"{S}.source_listing_revisions "
        f"(incarnation_id, fetched_at DESC, id DESC)"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX {S}.ix_slrev_incarnation_fetched")
