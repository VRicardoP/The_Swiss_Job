"""§4-LOCAL — ciclo de vida del manifiesto de migración (status).

El `portfolio_migration_manifest` (core0013) es DURABLE: sobrevive al proceso para que el
operador/GATE-C lea `migration_verdict` de ahí. Pero tras un ROLLBACK, la migración se deshace
y su fila de manifiesto quedaba con `verdict='ok'` — un veredicto OBSOLETO que el operador podía
atestar como GATE-C verde (P1 rev. externa §4-LOCAL). Se añade `status` (applied|rolled_back|
rollback_aborted): el rollback lo marca, y el procedimiento operativo solo acepta manifiestos
`applied` como fuente de `migration_verdict`.

Revision ID: core0014
Revises: core0013
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from jobhunt_core.config import settings

revision: str = "core0014"
down_revision: Union[str, None] = "core0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = settings.CORE_DB_SCHEMA  # "jobhunt"


def upgrade() -> None:
    # 'applied' por defecto: un manifiesto persistido es 'applied' hasta que un rollback lo
    # marque. Las filas previas (si las hubiera) se asumen aplicadas.
    op.add_column(
        "portfolio_migration_manifest",
        sa.Column(
            "status", sa.Text, nullable=False, server_default=sa.text("'applied'")
        ),
        schema=S,
    )


def downgrade() -> None:
    op.drop_column("portfolio_migration_manifest", "status", schema=S)
