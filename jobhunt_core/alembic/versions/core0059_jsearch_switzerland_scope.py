"""harvest_scopes: el ámbito de JSearch pasa de Estados Unidos a Suiza.

Decisión del propietario (2026-11-24): el núcleo busca empleo en Suiza, y el
ámbito verificado de JSearch pedía ofertas de Estados Unidos.

Como en core0058, el id de un ámbito se deriva de sus parámetros, así que la
fila se traslada conservando `enabled`, su estado y su historial. Si el ámbito
no existe (base nueva), no hace nada: lo creará `provision_jsearch`.

Revision ID: core0059
Revises: core0058
"""

import json
import uuid

import sqlalchemy as sa
from alembic import op

from jobhunt_core.config import settings

revision = "core0059"
down_revision = "core0058"
branch_labels = None
depends_on = None

S = settings.CORE_DB_SCHEMA
UNITED_STATES = {
    "query": "software developer",
    "country": "us",
    "date_posted": "week",
    "page": 1,
    "num_pages": 1,
}
SWITZERLAND = {**UNITED_STATES, "country": "ch"}
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
    _move(UNITED_STATES, SWITZERLAND)


def downgrade() -> None:
    _move(SWITZERLAND, UNITED_STATES)
