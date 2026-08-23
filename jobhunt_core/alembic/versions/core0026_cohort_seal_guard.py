"""core0026 — el SELLO de la cohorte también es inmutable + manifest obligatorio.

Revisión solo-código del cierre Nº2 (2026-08-23), BLOQUEANTES 2 y 3:

- B-2: el trigger de core0025 protegía los PARES pero no la fila que define
  el sello: `UPDATE labeled_dedup_cohorts SET frozen_at=NULL` reabría la
  cohorte, se reescribía un verdict y se restauraba el mismo frozen_at con
  otro manifest — sin rastro. Reproducido por el revisor. Ahora un trigger
  sobre labeled_dedup_cohorts prohíbe UPDATE/DELETE cuando OLD.frozen_at
  IS NOT NULL: la única transición permitida es NULL → sellado, una vez.
  Y el sello NO es retrodatable (agujero del autor + BLOQUEANTE 1 de la
  ronda 2): sellar exige frozen_at = statement_timestamp() — now() era
  burlable con una transacción abierta ANTES del instante real (now() es
  el timestamp de TRANSACCIÓN; reproducido por el revisor con 150 ms).
- B3 ronda 2 (+P-2 ronda 3): el lock que serializa el sello con los
  escritores de pares vive AQUÍ, en el trigger — ÚNICO dueño de la
  serialización. El helper NO toma locks propios (su antiguo LOCK TABLE
  invertía el orden frente al UPDATE directo y producía deadlock) y sella
  UPDATE-primero/INSERT-después para adquirir fila→pares como el DML.
- B2 ronda 2: 'null'::jsonb, arrays y escalares pasaban el CHECK
  (JSON null NO es NULL SQL) — el manifest exige jsonb_typeof = object.
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
            IF NEW.frozen_at IS NOT NULL THEN
                -- Sellar exige frozen_at = statement_timestamp(): now() es
                -- el timestamp de TRANSACCIÓN y una tx abierta antes del
                -- instante real retrodataba el sello (ronda 2, B-1) —
                -- movería el corte de elegibilidad y haría elegibles
                -- ciclos anteriores al freeze. (Solo VALIDA la intención;
                -- el valor persistido se canonicaliza tras el lock.)
                IF NEW.frozen_at <> statement_timestamp() THEN
                    RAISE EXCEPTION
                        'sello RETRODATADO en cohorte %: frozen_at debe ser statement_timestamp() (core0026)',
                        NEW.source;
                END IF;
                -- Serialización en la FRONTERA de la BD (ronda 2, B-3): el
                -- sello espera a los escritores de pares EN VUELO (entran
                -- al snapshot) sea cual sea la vía — helper o DML directo.
                LOCK TABLE {S}.labeled_dedup_pairs IN SHARE ROW EXCLUSIVE MODE;
                -- Ronda 3 P-1: el instante EFECTIVO del sello es DESPUÉS
                -- de drenar a los escritores — statement_timestamp() se
                -- fijó al INICIO del intento y la espera del lock puede
                -- ser arbitraria: un ciclo iniciado durante esa espera
                -- habría sido declarado elegible con un freeze aún no
                -- efectivo. Se persiste clock_timestamp() post-lock.
                NEW.frozen_at := clock_timestamp();
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
        CHECK (frozen_at IS NULL OR (jsonb_typeof(manifest) = 'object'
               AND manifest <> '{{}}'::jsonb))
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
