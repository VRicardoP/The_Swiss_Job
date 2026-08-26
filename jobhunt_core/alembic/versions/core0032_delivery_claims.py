"""core0032 — contador de RECLAMOS (`claims`), separado de `attempts`.

Auditoría G3 (2026-08-26, hipótesis G3-H-1, CONFIRMADA): desde que el intento
lo consume el RESULTADO y no el claim (G2-P3-4), una entrega cuyo payload MATA
al proceso del dispatcher (OOM, segfault del driver, bucle de reinicio) ya no
se auto-limita: se reclama, mata al worker, el lease caduca, se re-reclama…
indefinidamente y con `attempts` clavado en 0, así que el DEAD-LETTER por
agotamiento jamás llega. Y como el claim ordena por `next_attempt_at NULLS
FIRST`, ese mensaje ocupa la CABEZA de la cola: bloquea al resto.

No se puede volver a contar el intento en el claim sin deshacer G2-P3-4 (un
re-claim fantasma quemaría intentos sin que el transporte corriera nunca), así
que el veneno necesita su PROPIO contador:

- `claims`: +1 en cada reclamo; a 0 en cuanto la entrega produce un RESULTADO
  (`delivery._persist_attempts` / `mark_delivered`), de modo que mide RECLAMOS
  CONSECUTIVOS SIN resultado — nunca crece con un destino simplemente caído.
- Backfill `claims = attempts`: las filas vivas ya se reclamaron al menos una
  vez por intento consumido; parte de una cota INFERIOR honesta y jamás mete a
  una fila sana en el umbral de veneno (MAX_ATTEMPTS=8 <<
  DELIVERY_MAX_CLAIMS_WITHOUT_RESULT=25).

Idempotente: la columna se añade solo si no existe (IF NOT EXISTS) y el
backfill solo toca las filas cuyo `claims` sigue a 0.

Revision ID: core0032
Revises: core0031
Create Date: 2026-08-26
"""

from typing import Sequence, Union

from alembic import op

from jobhunt_core.config import settings

revision: str = "core0032"
down_revision: Union[str, None] = "core0031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = settings.CORE_DB_SCHEMA  # "jobhunt"


def upgrade() -> None:
    op.execute(
        f"ALTER TABLE {S}.integration_outbox_deliveries "
        f"ADD COLUMN IF NOT EXISTS claims integer NOT NULL DEFAULT 0"
    )
    # Cota INFERIOR honesta para lo preexistente: cada intento consumido
    # implica al menos un reclamo (el veneno, por definición, nunca consumió
    # ninguno, así que arranca en 0 y cuenta desde este despliegue).
    op.execute(
        f"UPDATE {S}.integration_outbox_deliveries "
        f"SET claims = attempts WHERE claims = 0 AND attempts > 0"
    )


def downgrade() -> None:
    op.execute(
        f"ALTER TABLE {S}.integration_outbox_deliveries DROP COLUMN IF EXISTS claims"
    )
