"""Indexa el filtro de ubicación del catálogo, que pasó a ser alcanzable.

DT-148 A18-01. `country`/`city` del `/v1` y `location` del evaluador de
búsquedas guardadas filtran `content->>'location'` como subcadena. El docstring
de `_location_conditions` lo decía sin rodeos —«Location conserva su coste por
fila»— y era tolerable mientras NADIE lo usaba: el BFF no exponía el filtro.

Al retirar el desplegable de cantones (que siempre acababa en 501) y poner en su
lugar un campo de ubicación, el filtro pasó a ser alcanzable desde la interfaz.
Medido en producción ANTES de este índice, con `EXPLAIN` y con reloj:

    EXPLAIN → Aggregate → Hash Join → Seq Scan on vacancies
                                    → Seq Scan on offer_revisions
                                        Filter: POSITION('remote' IN lower(...))

    location='Remote'        2.534 resultados en  86,4 s
    location='zzz-no-existe'      0 resultados en  44,9 s

Sobre 227.053 revisiones y un NAS con loadavg 10,85 en 2 CPUs. Un filtro que la
interfaz ofrece y que tarda minuto y medio es, en la práctica, otra forma de no
servirlo.

Dos cambios, y hacen falta LOS DOS: `position(x in y) > 0` **no lo puede servir
un índice trgm**, así que la condición se reescribió como `LIKE` escapado —el
mismo patrón y el mismo escapado que `q`, por el que `core0045` creó sus dos
índices gemelos— y aquí se crea el índice que la sirve.

Mismo molde que `core0045`, incluido el `lock_timeout`: construir un GIN sobre
una tabla que la cosecha está escribiendo toma un lock, y es mejor que la
migración falle rápido y se reintente en un hueco que bloquear un barrido.
Aditiva: no cambia ni la semántica del filtro ni un solo dato.

REGLA (disciplina Alembic, env.py): revisión APLICADA = INMUTABLE.

Revision ID: core0057
Revises: core0056
"""

from alembic import op

from jobhunt_core.config import settings

revision = "core0057"
down_revision = "core0056"
branch_labels = depends_on = None
S = settings.CORE_DB_SCHEMA


def upgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute(
        f"CREATE INDEX ix_offer_revisions_catalog_location ON {S}.offer_revisions "
        "USING gin (lower(coalesce(content->>'location','')) gin_trgm_ops)"
    )


def downgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute(f"DROP INDEX {S}.ix_offer_revisions_catalog_location")
