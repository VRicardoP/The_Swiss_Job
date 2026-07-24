"""B-01 — Estado y staging del CDC de la sombra (CONTRATOS_FASE_B.md §2).

Tablas [B] del buffer legacy→core, bloqueadas hasta el OK del compose
(concedido 2026-07-24):

- `shadow_capture_state`: la frontera snapshot↔LSN y el progreso, en UNA fila
  (id=1 con CHECK — §15bis: la sombra ensaya la maquinaria del cutover y la
  frontera queda registrada, no implícita).
- `shadow_change_log`: buffer idempotente de cambios. PK(lsn, seq_in_tx) —
  la re-entrega del slot (ack tras commit) colisiona ahí y se absorbe con
  DO NOTHING. `applied_at` lo sella el proyector (B-02); el índice PARCIAL
  sobre las filas sin aplicar es su cola de trabajo (la tabla retiene ciclos
  cerrados + 7 días: un índice completo sería casi todo ruido ya aplicado).
- `shadow_projection_batches`: marcas de LOTE del proyector — fuente de
  `latencia_p95` (§5: `offer_revisions.created_at` no enlaza con el cambio
  origen; la traza temporal es del lote).

LSN como BIGINT (decisión documentada, §2 admite PG_LSN o BIGINT): pg_lsn
'X/Y' = (X<<32)|Y — el MISMO entero que usa el protocolo de replicación de
psycopg2 (msg.data_start); orden total nativo, aritmética directa y sin tipo
custom en SQLAlchemy. La conversión vive en shadow/capture.py (lsn_to_int).

REGLA (disciplina Alembic, env.py): revisión APLICADA = INMUTABLE.

Revision ID: core0008b
Revises: core0008a
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

from jobhunt_core.config import settings

revision: str = "core0008b"
down_revision: Union[str, None] = "core0008a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = settings.CORE_DB_SCHEMA  # "jobhunt"
NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "shadow_capture_state",
        sa.Column("id", sa.SmallInteger, primary_key=True, autoincrement=False),
        sa.Column("slot_name", sa.Text, nullable=False),
        sa.Column("snapshot_lsn", sa.BigInteger, nullable=False),
        sa.Column(
            "snapshot_exported_at", sa.TIMESTAMP(timezone=True), nullable=False
        ),
        sa.Column("last_applied_lsn", sa.BigInteger, nullable=False),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=NOW
        ),
        sa.CheckConstraint("id = 1", name="ck_capture_state_single_row"),
        schema=S,
    )
    op.create_table(
        "shadow_change_log",
        sa.Column("lsn", sa.BigInteger, nullable=False),
        sa.Column("seq_in_tx", sa.Integer, nullable=False),
        sa.Column("src_table", sa.Text, nullable=False),
        sa.Column("op", sa.String(1), nullable=False),
        sa.Column("pk", sa.Text, nullable=False),
        sa.Column(
            "payload", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "received_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=NOW
        ),
        sa.Column("applied_at", sa.TIMESTAMP(timezone=True)),
        sa.PrimaryKeyConstraint("lsn", "seq_in_tx", name="pk_shadow_change_log"),
        sa.CheckConstraint("op IN ('I', 'U', 'D')", name="ck_change_log_op"),
        schema=S,
    )
    # Cola del proyector (B-02): consume en orden LSN SOLO lo no aplicado.
    op.create_index(
        "ix_shadow_change_unapplied",
        "shadow_change_log",
        ["lsn", "seq_in_tx"],
        schema=S,
        postgresql_where=sa.text("applied_at IS NULL"),
    )
    op.create_table(
        "shadow_projection_batches",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("first_lsn", sa.BigInteger, nullable=False),
        sa.Column("last_lsn", sa.BigInteger, nullable=False),
        sa.Column("min_received_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "started_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=NOW
        ),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True)),
        sa.Column(
            "changes", sa.Integer, nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "revisions_new", sa.Integer, nullable=False, server_default=sa.text("0")
        ),
        schema=S,
    )


def downgrade() -> None:
    # Orden inverso a la creación; el índice parcial cae con su tabla.
    for table in (
        "shadow_projection_batches",
        "shadow_change_log",
        "shadow_capture_state",
    ):
        op.drop_table(table, schema=S)
