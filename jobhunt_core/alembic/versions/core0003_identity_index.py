"""A-05 — Índice de búsqueda cross-source por url_normalized.

El re-enlace determinista (ADR-01 nivel 2) busca slots de OTRAS fuentes por
`url_normalized` solo: la UNIQUE(source_id, url_normalized) existente no sirve
(source_id lidera el btree). Revisión NUEVA — core0002 aplicada es INMUTABLE
(disciplina Alembic, plan §24 A-02).

Revision ID: core0003
Revises: core0002
"""

from typing import Sequence, Union

from alembic import op

from jobhunt_core.config import settings

revision: str = "core0003"
down_revision: Union[str, None] = "core0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = settings.CORE_DB_SCHEMA  # "jobhunt"


def upgrade() -> None:
    op.create_index(
        "ix_source_listings_url_normalized",
        "source_listings",
        ["url_normalized"],
        schema=S,
    )


def downgrade() -> None:
    op.drop_index("ix_source_listings_url_normalized", "source_listings", schema=S)
