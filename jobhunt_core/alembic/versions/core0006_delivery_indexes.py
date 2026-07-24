"""A-10 (2ª revisión) — Índices parciales del despacho del outbox.

El predicado del claim (pending vencidas ∪ inflight con lease caducado) y las
métricas de lag hacían seq scan sobre una tabla respaldada por un outbox
append-only ilimitado. Índices PARCIALES por estado: baratos de mantener
(solo filas vivas) y cubren claim + lag.

Revisión NUEVA — core0005 aplicada es INMUTABLE (disciplina Alembic).

Revision ID: core0006
Revises: core0005
"""

from typing import Sequence, Union

from alembic import op

from jobhunt_core.config import settings

revision: str = "core0006"
down_revision: Union[str, None] = "core0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = settings.CORE_DB_SCHEMA  # "jobhunt"


def upgrade() -> None:
    op.execute(
        f"CREATE INDEX ix_outbox_deliv_pending ON {S}.integration_outbox_deliveries "
        f"(next_attempt_at) WHERE state = 'pending'"
    )
    op.execute(
        f"CREATE INDEX ix_outbox_deliv_inflight ON {S}.integration_outbox_deliveries "
        f"(lease) WHERE state = 'inflight'"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX {S}.ix_outbox_deliv_inflight")
    op.execute(f"DROP INDEX {S}.ix_outbox_deliv_pending")
