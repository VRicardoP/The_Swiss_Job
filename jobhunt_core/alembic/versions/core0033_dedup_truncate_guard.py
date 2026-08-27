"""core0033 — el oráculo congelado tampoco se puede TRUNCAR.

Auditoría G9 P3-A: `core0025` hace inmutables los pares de una cohorte sellada
con un trigger `BEFORE INSERT OR UPDATE OR DELETE ... FOR EACH ROW`, y el acta
de la cohorte lo describe como «inmutabilidad FÍSICA verificada». No lo es:
`TRUNCATE` no dispara triggers de FILA, y el rol dueño del esquema
(`jobhunt_core`) tiene el privilegio —comprobado con `has_table_privilege`—.
Un `TRUNCATE jobhunt.labeled_dedup_pairs` vaciaba el oráculo entero saltándose
la guarda, y con el oráculo vacío las métricas de dedup salen con el centinela
`no_data`: el gate quedaría en rojo sin que nadie supiera por qué.

Se añade la guarda que faltaba, con el MISMO criterio que core0025 (la
inmutabilidad la activa el SELLO, no la existencia de la tabla):

- `labeled_dedup_pairs`: TRUNCATE prohibido si hay alguna cohorte congelada.
- `labeled_dedup_cohorts`: también — truncarla borraría los propios sellos y
  dejaría los pares "descongelados" por la puerta de atrás.

Sin cohortes congeladas el TRUNCATE sigue permitido (un entorno de desarrollo
que aún no ha sellado nada no queda maniatado), y la guarda se puede desmontar
igual que las de core0025/core0026: `ALTER TABLE ... DISABLE TRIGGER`, que es
DDL del owner y deja rastro.

Revision ID: core0033
Revises: core0032
Create Date: 2026-08-27
"""

from typing import Sequence, Union

from alembic import op

from jobhunt_core.config import settings

revision: str = "core0033"
down_revision: Union[str, None] = "core0032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = settings.CORE_DB_SCHEMA  # "jobhunt"

_TABLAS = ("labeled_dedup_pairs", "labeled_dedup_cohorts")


def upgrade() -> None:
    op.execute(
        f"""
        CREATE FUNCTION {S}.trg_dedup_frozen_truncate_guard() RETURNS trigger AS $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM {S}.labeled_dedup_cohorts
                WHERE frozen_at IS NOT NULL
            ) THEN
                RAISE EXCEPTION
                    'cohorte dedup CONGELADA: TRUNCATE de % está prohibido — el oráculo sellado es inmutable (core0033)',
                    TG_TABLE_NAME;
            END IF;
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for tabla in _TABLAS:
        op.execute(
            f"""
            CREATE TRIGGER {tabla}_truncate_guard
            BEFORE TRUNCATE ON {S}.{tabla}
            FOR EACH STATEMENT
            EXECUTE FUNCTION {S}.trg_dedup_frozen_truncate_guard()
            """
        )


def downgrade() -> None:
    for tabla in _TABLAS:
        op.execute(f"DROP TRIGGER {tabla}_truncate_guard ON {S}.{tabla}")
    op.execute(f"DROP FUNCTION {S}.trg_dedup_frozen_truncate_guard()")
