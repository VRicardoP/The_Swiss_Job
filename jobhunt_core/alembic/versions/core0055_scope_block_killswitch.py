"""source_scope_state: contador de BLOQUEOS y sello de apagado automático.

DT-144. El motor legacy pasaba cada barrido por `ComplianceEngine.can_scrape`
y, tras tres bloqueos seguidos (403/429/503), ponía la fuente en `is_allowed =
False`: dejaba de pedir. Core contaba fallos y alertaba, pero **nunca paraba**:
un portal que nos devuelve 403 cuatro veces al día seguiría recibiendo las
cuatro peticiones para siempre. Al traer los scrapers al núcleo, ese portero se
quedaba fuera.

`consecutive_blocks` cuenta sólo los fallos que el portal atribuye a un
bloqueo, separados de `consecutive_failures` a propósito: una caída de red y un
429 no son lo mismo y no deben apagar la misma fuente.

`disabled_by_blocks_at` existe porque la vigilancia sólo mira scopes
HABILITADOS: sin este sello, un scope apagado por el kill-switch desaparecería
de las alertas y el apagado se leería como silencio sano — exactamente el punto
ciego que `harvest/health.py` ya documenta.

Aditiva: las filas existentes arrancan a cero y sin sello, que es la verdad
(nadie las ha apagado).

Revision ID: core0055
Revises: core0054
"""

import sqlalchemy as sa
from alembic import op

from jobhunt_core.config import settings

revision = "core0055"
down_revision = "core0054"
branch_labels = None
depends_on = None

S = settings.CORE_DB_SCHEMA


def upgrade() -> None:
    op.add_column(
        "source_scope_state",
        sa.Column(
            "consecutive_blocks",
            sa.Integer,
            nullable=False,
            server_default=sa.text("0"),
        ),
        schema=S,
    )
    op.add_column(
        "source_scope_state",
        sa.Column("last_blocked_at", sa.TIMESTAMP(timezone=True)),
        schema=S,
    )
    op.add_column(
        "source_scope_state",
        sa.Column("disabled_by_blocks_at", sa.TIMESTAMP(timezone=True)),
        schema=S,
    )


def downgrade() -> None:
    op.drop_column("source_scope_state", "disabled_by_blocks_at", schema=S)
    op.drop_column("source_scope_state", "last_blocked_at", schema=S)
    op.drop_column("source_scope_state", "consecutive_blocks", schema=S)
