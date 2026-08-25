"""core0028 — url/url_normalized/apply_url a String(2048): fin de la cuarentena asimétrica.

Auditoría C5-P2-2: una url LEGAL en legacy (String(2048)) de 1001-2048
caracteres ponía en CUARENTENA la oferta ENTERA en el sink core
(MAX_URL_LEN=1000) en cada proyección — legal en un lado, pérdida total
silenciosa en el otro. Se alinea el contrato: columnas a 2048 (ALTER
solo-metadatos en Postgres; los índices UNIQUE de url_normalized no cambian
de semántica) y MAX_URL_LEN=2048 en el sink.

Revision ID: core0028
Revises: core0027
"""

from typing import Sequence, Union

from alembic import op

from jobhunt_core.config import settings

revision: str = "core0028"
down_revision: Union[str, None] = "core0027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = settings.CORE_DB_SCHEMA


def upgrade() -> None:
    for tabla, col in (
        ("source_listings", "url_normalized"),
        ("source_listing_incarnations", "url"),
        ("source_listing_incarnations", "apply_url"),
    ):
        op.execute(
            f"ALTER TABLE {S}.{tabla} ALTER COLUMN {col} TYPE VARCHAR(2048)"
        )


def downgrade() -> None:
    # No se estrecha: podría truncar datos ya persistidos.
    pass
