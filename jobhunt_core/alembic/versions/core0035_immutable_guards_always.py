"""core0035 — las guardas de inmutabilidad (ADR-04) también disparan en modo réplica.

Auditoría G11 P2-3: core0034 dejó fuera las otras diez guardas del esquema con un
motivo FALSO —que `ENABLE ALWAYS` las haría disparar durante un `pg_restore
--disable-triggers`, «que usa exactamente este mecanismo»—. Verificado ejecutando
contra este clúster (PostgreSQL 16.14): ese restore emite `ALTER TABLE … DISABLE
TRIGGER ALL`, no `session_replication_role`, y ese DDL apaga igualmente las 'A'.

Estas TRES son las únicas de las diez que conviene mover, y salen gratis:

- disparan SOLO en UPDATE (`BEFORE UPDATE … WHEN (OLD.col IS DISTINCT FROM
  NEW.col)`), y un restore data-only hace `COPY`/`INSERT` — nunca UPDATE. Cero
  roce con ninguna maniobra legítima (comprobado: el dump de una tabla del
  esquema no emite ni una sentencia UPDATE);
- son de FILA, no contadores: son las únicas de las diez que DENIEGAN algo.

Lo que cierran: en `SET session_replication_role='replica'` —sentencia de SESIÓN,
sin rastro en el catálogo, disponible para el superusuario `swissjob` con el que
se opera esta caja— un UPDATE masivo de `offer_revisions.vacancy_id`,
`source_listing_incarnations.vacancy_id` o `source_listing_revisions
.incarnation_id` reasignaba revisiones e incarnaciones a otra vacante sin que
nada lo impidiera. ADR-04 (v4) declara esas columnas INMUTABLES: merge se
resuelve por `merged_into` y el reciclado abriendo OTRA incarnación; una
reasignación directa rompe la coherencia del corpus en silencio, que es
exactamente lo que core0002 creó estas guardas para impedir.

Las otras SIETE se quedan en 'O' por una razón que sí se sostiene: seis son
contadores de `corpus_generation` a nivel de STATEMENT (no deniegan nada, y en
modo réplica lo que producen es una DESINCRONIZACIÓN del contador que
`shadow/projector.py` usa para decidir re-evaluaciones — un problema de
observabilidad del corpus, no de integridad) y `trg_ors_same_vacancy` dispara en
INSERT, o sea justo lo que un restore data-only sí hace.

ADVERTENCIA (la misma de core0034): `ENABLE ALWAYS` hace que la guarda dispare
también en un nodo que APLIQUE replicación lógica. Aquí no se replica hacia estas
tablas —`core-capture` LEE del slot, no escribe—; si algún día la sombra se
replicara hacia un nodo con estas tablas, hay que revisarlo.

Revision ID: core0035
Revises: core0034
Create Date: 2026-08-27
"""

from typing import Sequence, Union

from alembic import op

from jobhunt_core.config import settings

revision: str = "core0035"
down_revision: Union[str, None] = "core0034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = settings.CORE_DB_SCHEMA  # "jobhunt"

# (tabla, guarda) — las tres de `forbid_immutable_update` creadas en core0002.
_GUARDAS = (
    ("offer_revisions", "trg_offrev_vacancy_immutable"),
    ("source_listing_incarnations", "trg_incarnation_vacancy_immutable"),
    ("source_listing_revisions", "trg_slr_incarnation_immutable"),
)


def upgrade() -> None:
    for tabla, guarda in _GUARDAS:
        op.execute(f"ALTER TABLE {S}.{tabla} ENABLE ALWAYS TRIGGER {guarda}")


def downgrade() -> None:
    # Vuelta a 'O' (origin), el default de CREATE TRIGGER.
    for tabla, guarda in _GUARDAS:
        op.execute(f"ALTER TABLE {S}.{tabla} ENABLE TRIGGER {guarda}")
