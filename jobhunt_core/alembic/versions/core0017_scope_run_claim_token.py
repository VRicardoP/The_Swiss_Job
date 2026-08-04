"""Fencing del lease de cosecha: `claim_token` en source_harvest_runs.

Sin token de propiedad, tras un REARM por lease vencido (`claim_scope_run` re-arma un scope
'running' colgado >900s), el worker VIEJO conservaba permiso para ejecutar `finish_scope_run` —
que actualizaba por (run_id, scope_id) SIN condición — y SOBRESCRIBÍA el estado del worker NUEVO
que ya lo había reclamado; ambos, además, hacían tráfico externo (P1 rev. externa integral).

`claim_token` (UUID) se asigna en cada claim/rearm y se devuelve al ganador; `finish_scope_run`
(y cualquier heartbeat futuro) se condicionan a `claim_token = :token AND status = 'running'`, así
un worker desahuciado no puede cerrar el scope de otro.

Revision ID: core0017
Revises: core0016
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

from jobhunt_core.config import settings

revision: str = "core0017"
down_revision: Union[str, None] = "core0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = settings.CORE_DB_SCHEMA  # "jobhunt"


def upgrade() -> None:
    # NULLABLE: las filas previas (claims sin token) quedan con token NULL; finish_scope_run trata
    # token NULL como "sin dueño identificable" y no matchea (fail-closed). Los claims futuros lo fijan.
    op.add_column(
        "source_harvest_runs",
        sa.Column("claim_token", UUID(as_uuid=True), nullable=True),
        schema=S,
    )


def downgrade() -> None:
    op.drop_column("source_harvest_runs", "claim_token", schema=S)
