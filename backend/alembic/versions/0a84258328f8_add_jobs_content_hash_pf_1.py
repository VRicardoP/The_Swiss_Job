"""add jobs content_hash (PF.1)

Revision ID: 0a84258328f8
Revises: f7a9c1e2b3d4
Create Date: 2026-07-21 16:22:13.255855

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0a84258328f8'
down_revision: Union[str, None] = 'f7a9c1e2b3d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable: las filas existentes quedan con content_hash=NULL; el próximo
    # upsert de cada oferta lo rellena. Sin backfill (PF.1).
    op.add_column(
        "jobs", sa.Column("content_hash", sa.String(length=32), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("jobs", "content_hash")
