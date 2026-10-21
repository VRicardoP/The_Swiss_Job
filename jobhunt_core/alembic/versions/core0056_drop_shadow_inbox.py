"""Retira `shadow_inbox`: la tabla se quedó sin productor y sólo la tocaba su purga.

DT-148 B6, autorizado por el propietario el 2026-10-21.

`shadow_inbox` nació en `core0009` como el buzón IDEMPOTENTE de la Fase B: su
`PK(consumer_id, event_id)` absorbiendo la re-entrega *era* la demostración del
contrato ADR-06, y lo escribía `shadow/inbox.py` con `INSERT ON CONFLICT DO
NOTHING`. Ese paquete se retiró en `3ed6462` y con él el único escritor: desde
entonces la tabla no se escribe ni se lee, y lo único que la tocaba era el
`DELETE` por edad que `retention.py` lanzaba cada noche contra una tabla vacía.

Medido en producción ANTES del DROP (el punto lo exigía):

    filas  tamaño  FKs que la apuntan  vistas que la usan
        0   43 MB                   0                   0

Los 43 MB con cero filas son espacio asignado que `DELETE` nunca devuelve al
sistema. Retirar la tabla los recupera, que en un NAS con el volumen de sistema
al 84 % (deuda A19-18) no es irrelevante.

Por qué el `downgrade()` recrea la ESTRUCTURA y no promete los datos: un DROP de
tabla no es reversible. Se copia la definición literal de `core0009` —mismas
columnas, mismos defaults, mismo nombre de PK— para que volver atrás deje el
esquema exactamente como estaba; las filas, con 0 en producción, no existían.

Esto NO toca las otras seis tablas `shadow_*` que siguen en el esquema (260 MB
en total). Una de ellas, `shadow_change_log`, SOSTIENE PESO: `erasure.py` la
bloquea y machaca sus payloads en `scrub_capture`, que es parte del camino de
borrado GDPR. Agruparlas por el prefijo del nombre sería el error.

REGLA (disciplina Alembic, env.py): revisión APLICADA = INMUTABLE.

Revision ID: core0056
Revises: core0055
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

from jobhunt_core.config import settings

revision = "core0056"
down_revision = "core0055"
branch_labels = None
depends_on = None

S = settings.CORE_DB_SCHEMA


def upgrade() -> None:
    op.drop_table("shadow_inbox", schema=S)


def downgrade() -> None:
    # Definición LITERAL de core0009: volver atrás restituye el esquema, no las
    # filas (en producción eran cero).
    op.create_table(
        "shadow_inbox",
        sa.Column("consumer_id", sa.Text, nullable=False),
        sa.Column("event_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "payload", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "received_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("consumer_id", "event_id", name="pk_shadow_inbox"),
        schema=S,
    )
