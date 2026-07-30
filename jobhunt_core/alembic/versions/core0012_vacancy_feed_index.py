"""C-API-R — índice del keyset del feed de catálogo /v1/vacancies (Fase C).

El feed GET /v1/vacancies pagina por keyset (created_at DESC, id DESC) sobre
las vacantes ACTIVAS y presentables (archived_at IS NULL AND merged_into IS
NULL). Ningún índice existente lidera por ese orden: ix_vacancies_archived_at
(core0002) cubre solo archived_at. Índice PARCIAL alineado con el predicado
real (solo activas/no fundidas) y con el orden del keyset → range scan
O(página) del catálogo, sin seq scan del corpus en cada lectura /v1.

Revisión NUEVA — core0011 aplicada es INMUTABLE (disciplina Alembic).

Revision ID: core0012
Revises: core0011
"""

from typing import Sequence, Union

from alembic import op

from jobhunt_core.config import settings

revision: str = "core0012"
down_revision: Union[str, None] = "core0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = settings.CORE_DB_SCHEMA  # "jobhunt"


def upgrade() -> None:
    op.execute(
        f"CREATE INDEX ix_vacancies_feed_keyset ON {S}.vacancies "
        f"(created_at DESC, id DESC) "
        f"WHERE archived_at IS NULL AND merged_into IS NULL"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX {S}.ix_vacancies_feed_keyset")
