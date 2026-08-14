"""add storage signal to source_health (VD.3)

La salud V.0 clasifica ok|empty|error mirando la DESCARGA; una fuente puede
estar "sanísima" y no aportar ni una fila (stelle_admin: 7 descargadas,
0 guardadas por colisión de URL, estado `ok`). Se añade la señal de
PERSISTENCIA con racha propia — mismo criterio que la separación entre
`consecutive_errors` y `consecutive_empty`: acciones distintas, rachas
distintas.

Aditiva y barata: dos add_column con server_default sobre una tabla de ~15
filas — sin ventana de mantenimiento.

Revision ID: f2b7d94a1c63
Revises: d3e5a91c74b2
Create Date: 2026-08-14
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f2b7d94a1c63"
down_revision: Union[str, None] = "d3e5a91c74b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "source_health",
        sa.Column(
            "last_stored_count", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "source_health",
        sa.Column(
            "consecutive_unstored", sa.Integer(), nullable=False, server_default="0"
        ),
    )


def downgrade() -> None:
    op.drop_column("source_health", "consecutive_unstored")
    op.drop_column("source_health", "last_stored_count")
