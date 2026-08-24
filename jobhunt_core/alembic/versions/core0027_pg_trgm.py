"""core0027 — pg_trgm para el generador léxico de candidatos dedup (TRACK R.2b).

El examen del holdout midió recall 0.259: el ANN a SIM_MIN=0.95 no detecta
NINGÚN duplicado cross-portal real (0/9 también en development-2 — las
descripciones difieren o faltan entre portales y la similitud de texto
completo cae a 0.65–0.94). El generador léxico (token significativo de
empresa compartido + trigram de título + ubicación compatible) necesita
similarity() de pg_trgm. Extensión trusted: el owner puede crearla.

Revision ID: core0027
Revises: core0026
Create Date: 2026-08-24
"""

from typing import Sequence, Union

from alembic import op

revision: str = "core0027"
down_revision: Union[str, None] = "core0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")


def downgrade() -> None:
    # No se elimina: otra base de la misma BD podría usarla (idempotente
    # al re-aplicar; el drop sí podría romper a terceros).
    pass
