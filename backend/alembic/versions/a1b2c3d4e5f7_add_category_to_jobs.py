"""add category column to jobs table

Revision ID: a1b2c3d4e5f7
Revises: f1e2d3c4b5a6
Create Date: 2026-04-22 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f7"
down_revision: Union[str, None] = "f1e2d3c4b5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("category", sa.String(length=10), nullable=True),
    )
    op.create_index(op.f("ix_jobs_category"), "jobs", ["category"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_jobs_category"), table_name="jobs")
    op.drop_column("jobs", "category")
