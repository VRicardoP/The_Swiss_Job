"""Revisión externa parte 2 — entrega sombra real, heartbeat e intención de lote.

Tres piezas de la 2ª tanda de fixes (CONTRATOS_FASE_B.md §2/§5/§6/§8):

- `shadow_inbox` (P1-1b, decisión delegada [EJECUTADA]): inbox sombra
  PERSISTENTE e idempotente en el esquema del core — destino del transporte
  de producción de la Fase B (`shadow/inbox.py`, INSERT ON CONFLICT DO
  NOTHING). PK(consumer_id, event_id) = el contrato de consumo idempotente
  de ADR-06, demostrado EN CONTINUO (§8: el outbox core entrega SOLO al
  consumer sombra — cero efectos visibles). El transporte real HTTP al inbox
  del BFF llega con el cutover de Fase C.
- `shadow_capture_state.heartbeat_at` (P2-7): LIVENESS del consumidor CDC —
  lo actualiza cada keepalive del stream y cada tx aplicada. `updated_at` /
  `last_applied_lsn` quedan como PROGRESO DE DATOS: un healthcheck sobre
  ellos daba falso unhealthy con slot activo y días sin tráfico legacy.
  Backfill desde `updated_at`: no se inventa un latido más fresco del que
  se conoce.
- `shadow_projection_batches.recovered` (P2-5): la fila del lote pasa a ser
  INTENCIÓN DURABLE (se inserta al planificar, finished_at NULL; se
  finaliza en la misma tx que sella la última fuente). Una intención
  huérfana de una invocación muerta se cierra en la recuperación con
  finished_at=ahora y recovered=true — cuenta como lote LENTO en
  latencia_p95, jamás desaparece.

REGLA (disciplina Alembic, env.py): revisión APLICADA = INMUTABLE.

Revision ID: core0009
Revises: core0008b
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

from jobhunt_core.config import settings

revision: str = "core0009"
down_revision: Union[str, None] = "core0008b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = settings.CORE_DB_SCHEMA  # "jobhunt"
NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "shadow_inbox",
        sa.Column("consumer_id", sa.Text, nullable=False),
        sa.Column("event_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "payload", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "received_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=NOW,
        ),
        sa.PrimaryKeyConstraint("consumer_id", "event_id", name="pk_shadow_inbox"),
        schema=S,
    )
    op.add_column(
        "shadow_capture_state",
        sa.Column("heartbeat_at", sa.TIMESTAMP(timezone=True), nullable=True),
        schema=S,
    )
    # Backfill conservador: el último latido CONOCIDO es el último progreso.
    op.execute(f"UPDATE {S}.shadow_capture_state SET heartbeat_at = updated_at")
    op.add_column(
        "shadow_projection_batches",
        sa.Column(
            "recovered",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        schema=S,
    )


def downgrade() -> None:
    op.drop_column("shadow_projection_batches", "recovered", schema=S)
    op.drop_column("shadow_capture_state", "heartbeat_at", schema=S)
    op.drop_table("shadow_inbox", schema=S)
