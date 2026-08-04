"""§4-LOCAL — backfill del `seq` no fiable de los manifiestos previos a core0015.

`core0015` (seq) ya estaba PUBLICADA — no se reescribe (una BD que la aplicó no la re-ejecuta;
añadir el backfill ahí lo dejaría sin correr al hacer upgrade, P1 rev. externa 6). El backfill
va en una revisión NUEVA.

El `seq` (IDENTITY) de las filas que EXISTÍAN cuando core0015 se aplicó se asignó por el recorrido
FÍSICO de Postgres, NO por su orden de inserción — un CLUSTER previo lo invertiría, rompiendo el
LIFO. Como ese orden no se puede reconstruir (created_at=now() puede empatar), esas filas 'applied'
pasan a 'unknown': no atestables ni deshacibles por seq.

Ordenación operativa: el runbook aplica TODAS las migraciones ANTES de ejecutar el cutover, así
que cuando core0016 corre solo existen manifiestos PREVIOS (pre-seq, orden físico) — ningún
manifiesto NUEVO con seq fiable aún. Los manifiestos posteriores a core0016 (el cutover real)
reciben seq fiable y quedan 'applied'.

Revision ID: core0016
Revises: core0015
"""

from typing import Sequence, Union

from alembic import op

from jobhunt_core.config import settings

revision: str = "core0016"
down_revision: Union[str, None] = "core0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = settings.CORE_DB_SCHEMA  # "jobhunt"


def upgrade() -> None:
    op.execute(
        f"UPDATE {S}.portfolio_migration_manifest SET status = 'unknown' WHERE status = 'applied'"
    )


def downgrade() -> None:
    # Irreversible por dato: no se puede saber qué filas eran 'applied' antes del backfill. No-op.
    pass
