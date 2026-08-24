"""R.6 — jobs.apply_url: enlace de solicitud (ATS de la empresa).

La señal de mayor precisión posible para el dedup cross-portal del core
(ANALISIS_TRACK_R_FASE3): dos portales que enlazan al mismo ATS anuncian la
misma vacante. Hoy ningún dato la captura; esta columna la persiste para
los pares FUTUROS (no ayuda al examen congelado). Nullable, decorativa
(fuera de _CONTENT_FIELDS: no invalida embeddings), viaja al core vía la
whitelist del CDC hasta incarnations.apply_url (columna ya existente).

Revision ID: a1f2e3d4c5b6
Revises: 30d0bb87a4bd
"""

import sqlalchemy as sa
from alembic import op

revision = "a1f2e3d4c5b6"
down_revision = "30d0bb87a4bd"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("apply_url", sa.String(2048), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "apply_url")
