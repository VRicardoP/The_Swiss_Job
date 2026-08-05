"""La versión del corpus del intento pasa de timestamp a HUELLA: `corpus_fingerprint`.

`corpus_watermark` (core0019) era `max(offer_embeddings.created_at)` y NO versiona el corpus
evaluable (P1 rev. externa ronda 3): una vacante nueva que reutiliza un `text_hash` ya embebido no
crea fila de embedding; DESARCHIVAR una vacante antigua no mueve ninguna fecha; y archivar la más
reciente hace RETROCEDER el máximo. En los dos primeros casos la señal se quedaba APAGADA con
trabajo real pendiente.

La huella es `count|max(offer_embeddings.created_at)|max(offer_revisions.created_at)` sobre el
corpus ELEGIBLE del modelo (vacantes no archivadas ni fusionadas, con embedding de ese modelo), y se
compara por DESIGUALDAD, no por mayor-que: cualquier cambio del conjunto —alta, baja, desarchivado,
cambio de canónica o materialización de un embedding— la altera, y una versión que retrocede sigue
siendo un cambio. No hace falta que sea monotónica.

core0019 se publicó en este mismo ciclo, pero la disciplina es no reescribir migraciones publicadas:
la columna se sustituye aquí. La tabla es de estado DERIVADO (un intento sin registrar solo provoca
una re-evaluación idempotente), así que se recrea la columna sin backfill.

Revision ID: core0020
Revises: core0019
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from jobhunt_core.config import settings

revision: str = "core0020"
down_revision: Union[str, None] = "core0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = settings.CORE_DB_SCHEMA  # "jobhunt"


def upgrade() -> None:
    op.drop_column("profile_recovery_state", "corpus_watermark", schema=S)
    op.add_column(
        "profile_recovery_state",
        sa.Column("corpus_fingerprint", sa.Text(), nullable=False, server_default=""),
        schema=S,
    )
    # El default solo existe para poder añadir la columna NOT NULL sobre filas previas (estado
    # derivado): una huella vacía JAMÁS coincide con una real ⇒ re-evaluación, dirección segura.
    op.alter_column(
        "profile_recovery_state", "corpus_fingerprint", server_default=None, schema=S
    )


def downgrade() -> None:
    op.drop_column("profile_recovery_state", "corpus_fingerprint", schema=S)
    op.add_column(
        "profile_recovery_state",
        sa.Column(
            "corpus_watermark", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("now()"),
        ),
        schema=S,
    )
    op.alter_column(
        "profile_recovery_state", "corpus_watermark", server_default=None, schema=S
    )
