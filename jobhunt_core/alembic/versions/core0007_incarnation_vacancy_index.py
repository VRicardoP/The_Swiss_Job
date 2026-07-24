"""Auditoría final A-09→GATE A — índice de encarnaciones por vacante.

La query de listings activos por vacante (_vacancy_dtos, en get_vacancy y en
CADA página del feed) filtra source_listing_incarnations por vacancy_id +
ended_at IS NULL y NINGÚN índice lidera por vacancy_id (la FK no crea índice
en Postgres): seq scan O(corpus) en cada lectura /v1. Índice PARCIAL alineado
con el predicado real — solo filas activas, barato de mantener; acelera
también la reparación de primaries del sink (busca activas por vacante).

Revisión NUEVA — core0006 aplicada es INMUTABLE (disciplina Alembic).

Revision ID: core0007
Revises: core0006
"""

from typing import Sequence, Union

from alembic import op

from jobhunt_core.config import settings

revision: str = "core0007"
down_revision: Union[str, None] = "core0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = settings.CORE_DB_SCHEMA  # "jobhunt"


def upgrade() -> None:
    op.execute(
        f"CREATE INDEX ix_incarnation_vacancy_active ON {S}.source_listing_incarnations "
        f"(vacancy_id) WHERE ended_at IS NULL"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX {S}.ix_incarnation_vacancy_active")
