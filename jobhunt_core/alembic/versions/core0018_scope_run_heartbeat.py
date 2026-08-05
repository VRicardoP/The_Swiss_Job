"""Heartbeat del lease de cosecha: `heartbeat_at` en source_harvest_runs.

El lease (SCOPE_LEASE_S = 900) se medía SOLO desde `started_at`, que se fija en el claim y ya no
cambia: un fetch legítimamente largo (fuente lenta, muchas páginas) superaba el lease AUNQUE el
worker siguiera vivo, y otro `run_all` re-armaba el scope. El fencing por `claim_token` (core0017)
evita que el desahuciado corrompa el estado, pero NO evita que AMBOS hagan tráfico externo contra
la misma fuente — el residual conocido de la revisión integral, que deja de ser benigno en Fase D.

`heartbeat_at` lo renueva el worker vivo mientras dura su fetch; las condiciones de lease pasan a
`COALESCE(heartbeat_at, started_at)`, así una fila previa (heartbeat NULL) se comporta EXACTAMENTE
como hoy (started_at) y no hace falta backfill.

Revision ID: core0018
Revises: core0017
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from jobhunt_core.config import settings

revision: str = "core0018"
down_revision: Union[str, None] = "core0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = settings.CORE_DB_SCHEMA  # "jobhunt"


def upgrade() -> None:
    # NULLABLE y SIN backfill a propósito: COALESCE(heartbeat_at, started_at) da a las filas
    # preexistentes la semántica anterior exacta. El claim fija el primer valor.
    op.add_column(
        "source_harvest_runs",
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        schema=S,
    )


def downgrade() -> None:
    op.drop_column("source_harvest_runs", "heartbeat_at", schema=S)
