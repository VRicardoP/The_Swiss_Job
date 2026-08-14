"""add published_at to jobs (V.1 / ADR-10)

Fecha de publicación según el PORTAL, poblada por cada provider/scraper desde
su payload. Nullable SIN backfill: no hay de dónde sacarla para el corpus
existente y rellenarla con first_seen_at está PROHIBIDO (anularía la ventana
de bootstrap de ADR-10). None = "la fuente no la expone".

Aditiva y barata: add_column nullable + create_index sobre ~24 000 filas —
instantáneo, sin ventana de mantenimiento. El índice lo usan la ventana
semanal del ticket 2B y las consultas de caducidad.

Revision ID: 30d0bb87a4bd
Revises: f2b7d94a1c63
Create Date: 2026-08-14
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "30d0bb87a4bd"
down_revision: Union[str, None] = "f2b7d94a1c63"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        op.f("ix_jobs_published_at"), "jobs", ["published_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_jobs_published_at"), table_name="jobs")
    op.drop_column("jobs", "published_at")
