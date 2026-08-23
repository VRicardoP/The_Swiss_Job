"""core0026 — el SELLO de la cohorte también es inmutable + manifest obligatorio.

Revisión solo-código del cierre Nº2 (2026-08-23), BLOQUEANTES 2 y 3:

- B-2: el trigger de core0025 protegía los PARES pero no la fila que define
  el sello: `UPDATE labeled_dedup_cohorts SET frozen_at=NULL` reabría la
  cohorte, se reescribía un verdict y se restauraba el mismo frozen_at con
  otro manifest — sin rastro. Reproducido por el revisor. Ahora un trigger
  sobre labeled_dedup_cohorts prohíbe UPDATE/DELETE cuando OLD.frozen_at
  IS NOT NULL: la única transición permitida es NULL → sellado, una vez.
  Y el sello NO es retrodatable (agujero encontrado al preparar la
  re-confirmación): un INSERT/UPDATE que ponga frozen_at exige
  frozen_at = now() — un frozen_at en el pasado movería el corte de
  elegibilidad del gate y haría elegibles ciclos anteriores al freeze.
- B-3: `manifest={}` activaba igualmente el corte de elegibilidad. CHECK:
  una fila congelada exige manifest NO vacío (el pre-registro SHA-256).

Límite declarado (revisión): el OWNER de la tabla conserva TRUNCATE y DDL
(DISABLE TRIGGER) — la inmutabilidad protege frente a las rutas de la
aplicación, no frente al administrador; ese acto es deliberado y visible.

Revision ID: core0026
Revises: core0025
Create Date: 2026-08-23
"""

from typing import Sequence, Union

from alembic import op

from jobhunt_core.config import settings

revision: str = "core0026"
down_revision: Union[str, None] = "core0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = settings.CORE_DB_SCHEMA  # "jobhunt"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE FUNCTION {S}.trg_dedup_cohorts_seal_guard() RETURNS trigger AS $$
        BEGIN
            IF TG_OP <> 'INSERT' AND OLD.frozen_at IS NOT NULL THEN
                RAISE EXCEPTION
                    'cohorte dedup SELLADA: % — el sello es inmutable (core0026): ni descongelar, ni reescribir manifest, ni borrar',
                    OLD.source;
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            -- Sellar (INSERT o transición NULL→valor) exige frozen_at =
            -- now(): un sello RETRODATADO movería el corte de elegibilidad
            -- al pasado y haría elegibles ciclos anteriores al freeze.
            IF NEW.frozen_at IS NOT NULL AND NEW.frozen_at <> now() THEN
                RAISE EXCEPTION
                    'sello RETRODATADO en cohorte %: frozen_at debe ser now() (core0026)',
                    NEW.source;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER labeled_dedup_cohorts_frozen_guard
        BEFORE INSERT OR UPDATE OR DELETE ON {S}.labeled_dedup_cohorts
        FOR EACH ROW EXECUTE FUNCTION {S}.trg_dedup_cohorts_seal_guard()
        """
    )
    op.execute(
        f"""
        ALTER TABLE {S}.labeled_dedup_cohorts
        ADD CONSTRAINT ck_cohort_frozen_requires_manifest
        CHECK (frozen_at IS NULL OR manifest <> '{{}}'::jsonb)
        """
    )


def downgrade() -> None:
    op.execute(
        f"ALTER TABLE {S}.labeled_dedup_cohorts "
        f"DROP CONSTRAINT ck_cohort_frozen_requires_manifest"
    )
    op.execute(
        f"DROP TRIGGER labeled_dedup_cohorts_frozen_guard "
        f"ON {S}.labeled_dedup_cohorts"
    )
    op.execute(f"DROP FUNCTION {S}.trg_dedup_cohorts_seal_guard()")
