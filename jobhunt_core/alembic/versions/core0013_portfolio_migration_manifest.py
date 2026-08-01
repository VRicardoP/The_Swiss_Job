"""C-4 — manifiesto durable de la migración de durables del portfolio (Fase C).

Artefacto DURABLE de reconciliación del cutover del piloto (P2 rev. externa
C-4): la migración persiste ANTES del commit un manifiesto versionado con los
RESULTADOS ESPERADOS derivados INDEPENDIENTEMENTE del origen, los conteos/
checksums del destino, el inventario de vacantes NUEVAS vs REUTILIZADAS, los
conflictos (colisiones/degradaciones) y el veredicto de reconciliación. Así el
informe se conserva aunque el proceso muera tras el commit, y un error
DETERMINISTA (que pasaría rerun + comparación cross-BD porque ambos destinos
fallan igual) se detecta al contrastar destino contra el ESPERADO del origen.

Revisión NUEVA — core0012 aplicada es INMUTABLE (disciplina Alembic).

Revision ID: core0013
Revises: core0012
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

from jobhunt_core.config import settings

revision: str = "core0013"
down_revision: Union[str, None] = "core0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = settings.CORE_DB_SCHEMA  # "jobhunt"


def upgrade() -> None:
    op.create_table(
        "portfolio_migration_manifest",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True),
            nullable=False, server_default=sa.text("now()"),
        ),
        # 'ok' si el destino reconcilia con el esperado del origen; 'divergent'
        # si hay divergencias (el manifiesto las detalla) → abortar el cutover.
        sa.Column("verdict", sa.Text, nullable=False),
        # Esperado (origen) + real (destino) + inventario + conflictos + divergencias.
        sa.Column("manifest", JSONB, nullable=False),
        schema=S,
    )


def downgrade() -> None:
    op.drop_table("portfolio_migration_manifest", schema=S)
