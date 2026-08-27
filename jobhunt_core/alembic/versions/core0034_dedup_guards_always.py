"""core0034 — las guardas del oráculo también disparan en modo réplica.

Auditoría G10 P2-3: los `CREATE TRIGGER` de core0025/core0026/core0033 nacen con
`tgenabled = 'O'` (*origin*), que es el default. Un
`SET session_replication_role = 'replica'` —sentencia de SESIÓN, no DDL— los deja
INERTES a todos y no cambia el catálogo, así que **no deja rastro alguno**, a
diferencia del `ALTER TABLE … DISABLE TRIGGER` que esas mismas migraciones
documentan como vía de desmontaje legítima («DDL del owner, con rastro»).

Reproducido en una base desechable con réplica exacta de las cuatro guardas y de
la cohorte sellada: TRUNCATE, DELETE sin WHERE y UPDATE del sello se bloquean,
pero `SET session_replication_role='replica'; TRUNCATE labeled_dedup_pairs;`
vacía el oráculo y **deja el sello intacto** — nada parece roto. Con los pares a
cero las métricas de dedup salen con el centinela `no_data` y el gate se pone
rojo sin causa visible: literalmente el escenario que core0033 vino a impedir.
Con `ENABLE ALWAYS` el mismo ataque vuelve a fallar en las tres variantes.

Quién podía: solo un superusuario (`jobhunt_core` y `jobhunt_capture` reciben
`permission denied to set parameter "session_replication_role"`). Pero `swissjob`
—el rol con el que se opera esta caja y el que usan los runbooks— lo es, y el
modelo de amenaza de la guarda es justamente el borrado masivo accidental del
operador.

ALCANCE: las CUATRO guardas del ORÁCULO. Las otras 10 del esquema se quedan
fuera de ESTA migración por ser un cambio que no hace falta para cerrar la vía
del oráculo, no por el motivo que aquí se alegó primero.

CORRECCIÓN (auditoría G11 P2-3). El párrafo original decía que `ENABLE ALWAYS`
en las otras diez las habría hecho disparar durante un `pg_restore
--disable-triggers` «que usa exactamente este mecanismo». Es FALSO, y se ha
verificado ejecutando contra este mismo clúster (PostgreSQL 16.14):

  $ pg_dump --data-only --disable-triggers --table=jobhunt.corpus_generation
    ALTER TABLE jobhunt.corpus_generation DISABLE TRIGGER ALL;
    COPY jobhunt.corpus_generation ... FROM stdin;
    ALTER TABLE jobhunt.corpus_generation ENABLE TRIGGER ALL;

Ni un `SET session_replication_role`: el mecanismo es DDL del owner, y ese DDL
apaga TAMBIÉN las `ENABLE ALWAYS` (medido en base desechable: 'A' → 'D'). O sea
que `ENABLE ALWAYS` nunca habría estorbado a ningún restore.

Y de propina, el peligro que el párrafo falso ocultaba: el `ENABLE TRIGGER ALL`
con que el propio dump cierra devuelve las guardas a **'O'**, no a 'A'. Un
restore data-only de `labeled_dedup_pairs`/`_cohorts` DEGRADA EN SILENCIO estas
cuatro guardas y reabre el ataque. Por eso toda maniobra de datos termina
comprobando el catálogo: RUNBOOK §8 (`jobhunt_core/shadow/RUNBOOK.md`), con la
consulta y el DDL de re-armado. El test que lo fija es
`test_las_guardas_del_oraculo_disparan_tambien_en_modo_replica`.

Sobre el fondo —«¿queda alguna vía de borrado masivo abierta?»— la respuesta es
NO, y por un motivo que este párrafo tampoco daba: ninguna de las otras diez
DENIEGA nada (seis son contadores de `corpus_generation` a nivel de STATEMENT y
tres vigilan columnas inmutables SOLO en UPDATE). Las tres de inmutabilidad sí
pasan a `ENABLE ALWAYS` en core0035: disparan solo en UPDATE, que es algo que un
restore data-only (`COPY`) no hace jamás.

ADVERTENCIA: `ENABLE ALWAYS` hace que la guarda dispare también en un nodo que
APLIQUE replicación lógica. Aquí no se replica hacia estas tablas —`core-capture`
LEE del slot, no escribe—; si algún día la sombra se replicara hacia un nodo con
estas tablas, hay que revisarlo.

Revision ID: core0034
Revises: core0033
Create Date: 2026-08-27
"""

from typing import Sequence, Union

from alembic import op

from jobhunt_core.config import settings

revision: str = "core0034"
down_revision: Union[str, None] = "core0033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = settings.CORE_DB_SCHEMA  # "jobhunt"

# (tabla, guarda) — la inmutabilidad de fila (core0025/core0026) y la del
# statement TRUNCATE (core0033), que sin esto caían con la MISMA sentencia.
_GUARDAS = (
    ("labeled_dedup_pairs", "labeled_dedup_pairs_frozen_guard"),
    ("labeled_dedup_pairs", "labeled_dedup_pairs_truncate_guard"),
    ("labeled_dedup_cohorts", "labeled_dedup_cohorts_frozen_guard"),
    ("labeled_dedup_cohorts", "labeled_dedup_cohorts_truncate_guard"),
)


def upgrade() -> None:
    for tabla, guarda in _GUARDAS:
        op.execute(f"ALTER TABLE {S}.{tabla} ENABLE ALWAYS TRIGGER {guarda}")


def downgrade() -> None:
    # Vuelta a 'O' (origin), el default de CREATE TRIGGER.
    for tabla, guarda in _GUARDAS:
        op.execute(f"ALTER TABLE {S}.{tabla} ENABLE TRIGGER {guarda}")
