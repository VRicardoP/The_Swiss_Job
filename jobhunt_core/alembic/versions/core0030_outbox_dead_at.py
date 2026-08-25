"""core0030 — timestamp de transición a dead-letter (`dead_at`).

Auditoría G1 (2026-08-26, P2-3): el gate `outbox_dead` prometía «rojo si
hubo DEAD-LETTER en el ciclo» pero contaba el TOTAL HISTÓRICO de filas
`state='dead'` — dead es terminal y sin purga, así que UN evento muerto de
cualquier fecha dejaba la racha de 7 ciclos del GATE-SOMBRA inalcanzable
para siempre (pestillo sin ventana ni reset). Para acotar el conteo a la
ventana del ciclo hace falta el instante de la TRANSICIÓN, que no se
registraba (el UPDATE de delivery solo ponía last_error y soltaba el lease).

- `dead_at`: lo estampa `delivery.mark_failed` en la transición real.
- Backfill de los dead PREEXISTENTES a now(): quedan visibles (en rojo) en
  el ciclo en que aterriza esta migración — el operador los ve UNA vez — y
  dejan de bloquear los ciclos siguientes.

Revision ID: core0030
Revises: core0029
Create Date: 2026-08-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from jobhunt_core.config import settings

revision: str = "core0030"
down_revision: Union[str, None] = "core0029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = settings.CORE_DB_SCHEMA  # "jobhunt"


def upgrade() -> None:
    op.add_column(
        "integration_outbox_deliveries",
        sa.Column("dead_at", sa.TIMESTAMP(timezone=True)),
        schema=S,
    )
    # Dead históricos: visibles una vez (ciclo de la migración), no para siempre.
    op.execute(
        f"UPDATE {S}.integration_outbox_deliveries "
        f"SET dead_at = now() WHERE state = 'dead' AND dead_at IS NULL"
    )


def downgrade() -> None:
    op.drop_column("integration_outbox_deliveries", "dead_at", schema=S)
