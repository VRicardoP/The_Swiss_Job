"""add last_success_at to source_compliance

Revision ID: f6a8b9c1d2e3
Revises: e5f6a8b9c1d2
Create Date: 2026-05-29 16:00:00.000000

Necesario para que el healthcheck de la watchlist detecte scrapers
silenciosos (sin éxito en > 24h), no solo bloqueos. last_blocked_at
únicamente captura 403/429 pero NO scrapers que nunca corren (Celery beat
caído, source_key sin scraper registrado, HTML cambiado a 0 jobs).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f6a8b9c1d2e3"
down_revision: Union[str, None] = "e5f6a8b9c1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "source_compliance",
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("source_compliance", "last_success_at")
