"""core0037 — inmutabilidad física del oráculo y de ciclos sellados.

Los helpers ya rechazaban cambios en sets congelados y recomputaciones de
ciclos sellados, pero un DML directo podía saltárselos. Estas guardas llevan
el invariante a PostgreSQL. Un recomputo explícito sigue siendo posible, pero
marca el ciclo como recomputed_at antes de mutarlo y por ello nunca suma a la
racha. La poda acotada de samples del outbox sigue permitida.
"""

from typing import Sequence, Union

from alembic import op

from jobhunt_core.config import settings

revision: str = "core0037"
down_revision: Union[str, None] = "core0036"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = settings.CORE_DB_SCHEMA


def upgrade() -> None:
    op.execute(
        f"""
        CREATE FUNCTION {S}.trg_labeled_set_guard() RETURNS trigger AS $$
        BEGIN
            IF TG_OP <> 'INSERT' AND OLD.frozen_at IS NOT NULL THEN
                IF TG_OP = 'DELETE' AND pg_trigger_depth() > 1 THEN
                    RETURN OLD;
                END IF;
                RAISE EXCEPTION
                    'labeled_set congelado: % — no se puede modificar ni borrar',
                    OLD.id;
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            IF NEW.frozen_at IS NOT NULL THEN
                IF NEW.frozen_at <> statement_timestamp() THEN
                    RAISE EXCEPTION
                        'sello retrodatado en labeled_set %', NEW.id;
                END IF;
                LOCK TABLE {S}.labeled_judgments
                    IN SHARE ROW EXCLUSIVE MODE;
                NEW.frozen_at := clock_timestamp();
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER labeled_sets_frozen_guard
        BEFORE INSERT OR UPDATE OR DELETE ON {S}.labeled_sets
        FOR EACH ROW EXECUTE FUNCTION {S}.trg_labeled_set_guard()
        """
    )
    op.execute(
        f"ALTER TABLE {S}.labeled_sets ENABLE ALWAYS TRIGGER labeled_sets_frozen_guard"
    )
    op.execute(
        f"""
        CREATE FUNCTION {S}.trg_labeled_judgment_guard() RETURNS trigger AS $$
        DECLARE
            old_set uuid;
            new_set uuid;
        BEGIN
            old_set := CASE WHEN TG_OP = 'INSERT' THEN NULL ELSE OLD.set_id END;
            new_set := CASE WHEN TG_OP = 'DELETE' THEN NULL ELSE NEW.set_id END;
            IF (
                old_set IS NOT NULL
                AND EXISTS (
                    SELECT 1 FROM {S}.labeled_sets
                    WHERE id = old_set AND frozen_at IS NOT NULL
                )
            ) OR (
                new_set IS NOT NULL
                AND EXISTS (
                    SELECT 1 FROM {S}.labeled_sets
                    WHERE id = new_set AND frozen_at IS NOT NULL
                )
            ) THEN
                IF TG_OP = 'DELETE' AND pg_trigger_depth() > 1 THEN
                    RETURN OLD;
                END IF;
                RAISE EXCEPTION
                    'juicios de labeled_set congelado: escritura rechazada';
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER labeled_judgments_frozen_guard
        BEFORE INSERT OR UPDATE OR DELETE ON {S}.labeled_judgments
        FOR EACH ROW EXECUTE FUNCTION {S}.trg_labeled_judgment_guard()
        """
    )
    op.execute(
        f"ALTER TABLE {S}.labeled_judgments "
        "ENABLE ALWAYS TRIGGER labeled_judgments_frozen_guard"
    )
    op.execute(
        f"""
        CREATE FUNCTION {S}.trg_sealed_metric_guard() RETURNS trigger AS $$
        BEGIN
            IF OLD.finished_at IS NULL THEN
                RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
            END IF;
            IF TG_OP = 'DELETE' THEN
                IF OLD.details ? 'recomputed_at' THEN
                    RETURN OLD;
                END IF;
                RAISE EXCEPTION 'métrica sellada: no se puede borrar';
            END IF;
            IF OLD.details ? 'recomputed_at'
               OR NEW.details ? 'recomputed_at' THEN
                RETURN NEW;
            END IF;
            IF NEW.value = OLD.value
               AND NEW.finished_at = OLD.finished_at
               AND NEW.started_at = OLD.started_at
               AND jsonb_typeof(OLD.details->'samples') = 'array'
               AND NEW.details = (OLD.details - 'samples')
                   || jsonb_build_object(
                       'samples_pruned',
                       jsonb_array_length(OLD.details->'samples')
                   ) THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION
                'métrica sellada: solo recomputo trazado o poda de samples';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER shadow_cycle_metrics_sealed_guard
        BEFORE UPDATE OR DELETE ON {S}.shadow_cycle_metrics
        FOR EACH ROW EXECUTE FUNCTION {S}.trg_sealed_metric_guard()
        """
    )
    op.execute(
        f"ALTER TABLE {S}.shadow_cycle_metrics "
        "ENABLE ALWAYS TRIGGER shadow_cycle_metrics_sealed_guard"
    )


def downgrade() -> None:
    for table, trigger in (
        ("shadow_cycle_metrics", "shadow_cycle_metrics_sealed_guard"),
        ("labeled_judgments", "labeled_judgments_frozen_guard"),
        ("labeled_sets", "labeled_sets_frozen_guard"),
    ):
        op.execute(f"DROP TRIGGER {trigger} ON {S}.{table}")
    for function in (
        "trg_sealed_metric_guard",
        "trg_labeled_judgment_guard",
        "trg_labeled_set_guard",
    ):
        op.execute(f"DROP FUNCTION {S}.{function}()")
