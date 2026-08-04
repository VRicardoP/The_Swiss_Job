"""§4-LOCAL — ordinal monotónico del manifiesto (seq) para el rollback LIFO.

`core0014` (status) ya estaba PUBLICADA — no se reescribe (una BD que la aplicó no la re-ejecuta;
añadir `seq` ahí dejaría la columna ausente al hacer upgrade → UndefinedColumn en _validate_
manifest, P1 rev. externa 4). El ordinal va en una revisión NUEVA.

`created_at` usa `now()`, CONSTANTE durante toda la transacción → dos migraciones en la MISMA tx
empatarían y el guard LIFO `created_at > :ts` no vería al gemelo; el UUID no ordena por inserción.
`seq` (IDENTITY) es un orden TOTAL de inserción. persist_manifest INSERTA sin fijar seq →
autogenerado; dos INSERT en la misma tx obtienen seq distintos y crecientes.

Revision ID: core0015
Revises: core0014
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from jobhunt_core.config import settings

revision: str = "core0015"
down_revision: Union[str, None] = "core0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = settings.CORE_DB_SCHEMA  # "jobhunt"


def upgrade() -> None:
    op.add_column(
        "portfolio_migration_manifest",
        sa.Column("seq", sa.BigInteger, sa.Identity(always=False, start=1), nullable=False),
        schema=S,
    )


def downgrade() -> None:
    op.drop_column("portfolio_migration_manifest", "seq", schema=S)
