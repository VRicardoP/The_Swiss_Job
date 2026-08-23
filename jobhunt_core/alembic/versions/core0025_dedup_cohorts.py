"""core0025 — cohortes de evaluación dedup CONGELABLES (auditoría Nº2, B-1).

La auditoría externa del cierre (2026-08-23, BLOQUEANTE 2) demostró que
`labeled_dedup_pairs` no tenía mecanismo de congelado: `freeze_set()` congela
sets de RELEVANCIA (labeled_sets), pero los pares dedup admitían UPDATE y
DELETE después de evaluar — "se congela" del protocolo del holdout no
correspondía a ninguna operación real. Esta migración lo materializa:

- `labeled_dedup_cohorts`: una fila por `source` de pares (development,
  holdout…) con `frozen_at` y `manifest` (hashes SHA-256 de los artefactos
  del muestreo — protocolo, SQL, CSV, mapa, hoja — para el pre-registro).
- Trigger-guard sobre `labeled_dedup_pairs`: INSERT/UPDATE/DELETE de un par
  cuya cohorte esté congelada ⇒ EXCEPTION. Inmutabilidad en la BD, no en la
  documentación. Un UPDATE que MUEVA un par hacia una cohorte congelada
  también se bloquea (comprueba OLD y NEW).

Revision ID: core0025
Revises: core0024
Create Date: 2026-08-23
"""

from typing import Sequence, Union

from alembic import op

from jobhunt_core.config import settings

revision: str = "core0025"
down_revision: Union[str, None] = "core0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = settings.CORE_DB_SCHEMA  # "jobhunt"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE {S}.labeled_dedup_cohorts (
            source     VARCHAR(64) PRIMARY KEY,
            frozen_at  TIMESTAMPTZ,
            manifest   JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION {S}.trg_dedup_pairs_frozen_guard() RETURNS trigger AS $$
        DECLARE
            comprobar text[];
            src text;
        BEGIN
            IF TG_OP = 'INSERT' THEN
                comprobar := ARRAY[NEW.source];
            ELSIF TG_OP = 'DELETE' THEN
                comprobar := ARRAY[OLD.source];
            ELSE
                comprobar := ARRAY[OLD.source, NEW.source];
            END IF;
            FOREACH src IN ARRAY comprobar LOOP
                IF EXISTS (
                    SELECT 1 FROM {S}.labeled_dedup_cohorts c
                    WHERE c.source = src AND c.frozen_at IS NOT NULL
                ) THEN
                    RAISE EXCEPTION
                        'cohorte dedup CONGELADA: % — labeled_dedup_pairs es inmutable tras el freeze (core0025)',
                        src;
                END IF;
            END LOOP;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER labeled_dedup_pairs_frozen_guard
        BEFORE INSERT OR UPDATE OR DELETE ON {S}.labeled_dedup_pairs
        FOR EACH ROW EXECUTE FUNCTION {S}.trg_dedup_pairs_frozen_guard()
        """
    )


def downgrade() -> None:
    op.execute(
        f"DROP TRIGGER labeled_dedup_pairs_frozen_guard ON {S}.labeled_dedup_pairs"
    )
    op.execute(f"DROP FUNCTION {S}.trg_dedup_pairs_frozen_guard()")
    op.execute(f"DROP TABLE {S}.labeled_dedup_cohorts")
