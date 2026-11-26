"""source_scope_state: sellos de los últimos barridos EXHAUSTIVOS (D7).

D7 cierra las encarnaciones que desaparecen de su portal, pero sólo en fuentes
cuyo productor recorre el listado ENTERO y lo declara (`FetchResult.exhaustive`).
`last_complete_at` no basta: guarda un solo instante, y «completo» incluye
barridos que terminan por el presupuesto de páginas declarado (irishjobs, NAV)
o por una ventana de fechas (Job-Room), que no ven el listado entero.

`exhaustive_sweep_at` guarda, del más reciente al más antiguo, el `now()` de la
transacción de persistencia de los últimos `EXHAUSTIVE_SWEEPS` barridos
exhaustivos. Es el MISMO `now()` que el sink escribe en `last_seen_at` de lo
que ese barrido vio, así que «no vista en los últimos N barridos» es
exactamente `last_seen_at < exhaustive_sweep_at[N]`.

Aditiva y sólo de catálogo (`ADD COLUMN` con default constante): las filas
existentes arrancan sin sellos, que es la verdad, y el cierre no empieza en
ninguna fuente hasta que acumule N barridos exhaustivos tras el despliegue.

Revision ID: core0060
Revises: core0059
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

from jobhunt_core.config import settings

revision = "core0060"
down_revision = "core0061"
branch_labels = None
depends_on = None

S = settings.CORE_DB_SCHEMA


def upgrade() -> None:
    op.add_column(
        "source_scope_state",
        sa.Column(
            "exhaustive_sweep_at",
            ARRAY(sa.TIMESTAMP(timezone=True)),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        schema=S,
    )


def downgrade() -> None:
    op.drop_column("source_scope_state", "exhaustive_sweep_at", schema=S)
