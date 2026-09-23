"""job_title_languages — idioma derivado, resuelto una vez y persistido

Saca la deteccion masiva de idioma del camino de respuesta (punto 5): el
router deducia el idioma de CADA oferta servida para el indicador de la UI,
50,1 ms por titulo y ~90 s por peticion. Aqui se resuelve UNA vez por titulo
distinto, en segundo plano, y sobrevive a los reinicios — que es lo que la
memoizacion en proceso no podia dar.

Semantica de los tres estados en `models/title_language.py`. En corto:
fila ausente = nunca visto; `language IS NULL` = encolado; `language = ''` =
resuelto como DESCONOCIDO (y por eso no se vuelve a intentar).

ADITIVA Y REVERSIBLE: tabla nueva, nada que rellenar ni migrar. Si se
desplegara el codigo sin la tabla, el router seguiria sirviendo — sin
indicador de idioma— porque `record_pending` es best-effort y `lookup`
devolveria vacio. El `downgrade` la borra entera: sus datos son DERIVADOS y
se regeneran solos.

Revision ID: d3a7c1f60b84
Revises: c57f2341b012
Create Date: 2026-09-23

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d3a7c1f60b84"
down_revision: Union[str, None] = "c57f2341b012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "job_title_languages",
        sa.Column("title", sa.String(length=500), primary_key=True),
        sa.Column("language", sa.String(length=16), nullable=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    # La tarea de fondo pide SIEMPRE los pendientes mas antiguos primero. El
    # indice parcial cubre justo esa consulta y solo indexa las filas
    # pendientes, que son una minoria decreciente: en regimen permanente esta
    # practicamente vacio.
    op.create_index(
        "ix_job_title_languages_pending",
        "job_title_languages",
        ["created_at"],
        postgresql_where=sa.text("language IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_job_title_languages_pending", table_name="job_title_languages")
    op.drop_table("job_title_languages")
