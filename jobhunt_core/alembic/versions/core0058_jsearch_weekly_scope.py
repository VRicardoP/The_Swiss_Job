"""harvest_scopes: el ámbito de JSearch pasa a una página de la última semana.

Fase de ahorro de cuota (2026-11-24). El ámbito verificado de JSearch pedía 10
páginas del último mes; RapidAPI cobra una petición por página. Pasa a una
página de la última semana, con cadencia semanal.

El id de un ámbito se deriva de sus parámetros (`provision_scopes.scope_id`),
así que cambiar los parámetros cambia el id. La fila se traslada conservando
`enabled`, su estado y su historial de ejecuciones: se crea la fila nueva, se
reapuntan las dos tablas hijas y se borra la antigua. Si el ámbito no existe
(base nueva), no hace nada: lo creará `provision_jsearch`.

Revision ID: core0058
Revises: core0057
"""

import json
import uuid

import sqlalchemy as sa
from alembic import op

from jobhunt_core.config import settings

revision = "core0058"
down_revision = "core0057"
branch_labels = None
depends_on = None

S = settings.CORE_DB_SCHEMA
MONTHLY = {
    "query": "software developer",
    "country": "us",
    "date_posted": "month",
    "page": 1,
    "num_pages": 10,
}
WEEKLY = {**MONTHLY, "date_posted": "week", "num_pages": 1}
CHILD_TABLES = ("source_scope_state", "source_harvest_runs")


def _scope_id(params: dict) -> uuid.UUID:
    # Misma derivación que provision_scopes.scope_id, congelada aquí.
    value = json.dumps(params, sort_keys=True, separators=(",", ":"))
    return uuid.uuid5(uuid.NAMESPACE_URL, f"jobhunt:jsearch:{value}")


def _move(source: dict, target: dict) -> None:
    bind = op.get_bind()
    ids = {"old": _scope_id(source), "new": _scope_id(target)}
    moved = bind.execute(
        sa.text(
            f"INSERT INTO {S}.harvest_scopes (id, source_id, params, tier, enabled) "
            "SELECT :new, source_id, CAST(:params AS jsonb), tier, enabled "
            f"FROM {S}.harvest_scopes WHERE id = :old"
        ),
        {**ids, "params": json.dumps(target)},
    ).rowcount
    if not moved:
        return
    for table in CHILD_TABLES:
        bind.execute(
            sa.text(f"UPDATE {S}.{table} SET scope_id = :new WHERE scope_id = :old"),
            ids,
        )
    bind.execute(sa.text(f"DELETE FROM {S}.harvest_scopes WHERE id = :old"), ids)


def upgrade() -> None:
    _move(MONTHLY, WEEKLY)


def downgrade() -> None:
    _move(WEEKLY, MONTHLY)
