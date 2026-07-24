"""A-10 — destination al ancho de consumers.name.

Auditoría A-10: `integration_outbox_deliveries.destination` era VARCHAR(60)
pero el destino ES el nombre del consumer (VARCHAR(100)) — un consumer de
61-100 chars reventaba el INSERT de la entrega y, al ir en la MISMA
transacción, revertía la evaluación COMPLETA (matching sin persistir jamás
para ese tenant). Se alinean los anchos.

Revisión NUEVA — core0004 aplicada es INMUTABLE (disciplina Alembic).

Revision ID: core0005
Revises: core0004
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from jobhunt_core.config import settings

revision: str = "core0005"
down_revision: Union[str, None] = "core0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = settings.CORE_DB_SCHEMA  # "jobhunt"


def upgrade() -> None:
    op.alter_column(
        "integration_outbox_deliveries", "destination",
        type_=sa.String(100), existing_type=sa.String(60),
        existing_nullable=False, schema=S,
    )


def downgrade() -> None:
    # Best-effort: falla si existen destinos > 60 (correcto: no se truncan).
    op.alter_column(
        "integration_outbox_deliveries", "destination",
        type_=sa.String(60), existing_type=sa.String(100),
        existing_nullable=False, schema=S,
    )
