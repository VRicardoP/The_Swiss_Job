"""core0029 — C-4: escrituras vivas de candidaturas y búsquedas guardadas.

ALTERs MÍNIMOS sobre las tablas de core0011 (DISEÑO C-4 v2.1, sección Tablas):
- `applications`: +updated_at (timestamptz NOT NULL DEFAULT now()) y
  +revision (integer NOT NULL DEFAULT 0 — las filas migradas quedan en 0; las
  filas VIVAS que crea /v1 arrancan en 1, Decisión 6) + índice
  (profile_id, created_at, id) para la rama application del cursor compuesto.
- `saved_searches`: ídem (+updated_at, +revision, índice keyset).
- `profile_vacancy_state`: índice PARCIAL (profile_id, saved_at, vacancy_id)
  WHERE saved_at IS NOT NULL — rama bookmark del cursor compuesto (Decisión
  10). Sin columnas nuevas.
- SIN uq(profile_id, name) en saved_searches (H10: homónimas legítimas; el
  candado del POST vivo es la Idempotency-Key requerida — R2-8), SIN
  consumer_id (H5), SIN external_ref (H2), SIN tablas nuevas.

Revision ID: core0029
Revises: core0028
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from jobhunt_core.config import settings

revision: str = "core0029"
down_revision: Union[str, None] = "core0028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = settings.CORE_DB_SCHEMA
NOW = sa.text("now()")

# Las dos tablas reciben EXACTAMENTE los mismos ALTERs (updated_at + revision
# + índice keyset por perfil) — definición única para que no diverjan.
_KEYSET_INDEXES = (
    ("ix_applications_feed_keyset", "applications"),
    ("ix_saved_searches_feed_keyset", "saved_searches"),
)


def upgrade() -> None:
    for _ix, table in _KEYSET_INDEXES:
        op.add_column(
            table,
            sa.Column(
                "updated_at", sa.TIMESTAMP(timezone=True),
                nullable=False, server_default=NOW,
            ),
            schema=S,
        )
        op.add_column(
            table,
            sa.Column(
                "revision", sa.Integer, nullable=False,
                server_default=sa.text("0"),
            ),
            schema=S,
        )
    for ix, table in _KEYSET_INDEXES:
        op.create_index(
            ix, table, ["profile_id", "created_at", "id"], schema=S
        )
    # Rama bookmark del GET compuesto: solo filas CON saved_at (parcial).
    op.create_index(
        "ix_pvs_saved_feed_keyset",
        "profile_vacancy_state",
        ["profile_id", "saved_at", "vacancy_id"],
        schema=S,
        postgresql_where=sa.text("saved_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_pvs_saved_feed_keyset", "profile_vacancy_state", schema=S)
    for ix, table in _KEYSET_INDEXES:
        op.drop_index(ix, table, schema=S)
    for _ix, table in _KEYSET_INDEXES:
        op.drop_column(table, "revision", schema=S)
        op.drop_column(table, "updated_at", schema=S)
