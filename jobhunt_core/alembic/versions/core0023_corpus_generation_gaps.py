"""Cierra los huecos de cobertura de los triggers de generación del corpus (core0022).

Comprobado contra PostgreSQL 16 (experimento con una tabla particionada y un contador):

1. **`TRUNCATE` no dispara** un trigger AFTER INSERT/UPDATE/DELETE. Vaciar `vacancies` u
   `offer_embeddings` cambiaba el corpus SIN mover la generación ⇒ los intentos registrados
   seguirían dándose por válidos contra un corpus vacío. Se añaden triggers AFTER TRUNCATE.
2. **Una escritura DIRECTA a una partición** de `offer_embeddings` (p. ej.
   `INSERT INTO offer_embeddings_<hex> ...` en un backfill manual) NO dispara el trigger de
   SENTENCIA del padre: los triggers de sentencia solo se disparan por la tabla nombrada en la
   sentencia (el enrutado de filas del padre no los propaga). Hoy ningún camino del código escribe
   así —`embeddings.py` inserta siempre por el padre—, pero un backfill de operación o un cambio
   futuro lo haría en silencio. Se replican los triggers en las particiones EXISTENTES;
   `embeddings.register_model` hace lo propio con las que cree a partir de ahora.

No hay hueco equivalente en `vacancies` (no está particionada) ni al soltar una partición: el
camino de retirada de un modelo borra por el PADRE antes del `DROP TABLE` de la partición (que ya
queda vacía), y ese DELETE sí bumpea.

Revision ID: core0023
Revises: core0022
"""

from typing import Sequence, Union

from alembic import op

from jobhunt_core.config import settings

revision: str = "core0023"
down_revision: Union[str, None] = "core0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = settings.CORE_DB_SCHEMA  # "jobhunt"


def upgrade() -> None:
    for tabla in ("offer_embeddings", "vacancies"):
        op.execute(
            f"""
            CREATE TRIGGER trg_corpus_generation_truncate_{tabla}
            AFTER TRUNCATE ON {S}.{tabla}
            FOR EACH STATEMENT EXECUTE FUNCTION {S}.bump_corpus_generation()
            """
        )
    # Particiones YA existentes de offer_embeddings (las futuras las cubre register_model).
    op.execute(
        f"""
        DO $$
        DECLARE parte text;
        BEGIN
            FOR parte IN
                SELECT c.relname
                  FROM pg_inherits i
                  JOIN pg_class c ON c.oid = i.inhrelid
                  JOIN pg_class padre ON padre.oid = i.inhparent
                  JOIN pg_namespace n ON n.oid = padre.relnamespace
                 WHERE padre.relname = 'offer_embeddings' AND n.nspname = '{S}'
            LOOP
                EXECUTE format(
                    'CREATE OR REPLACE TRIGGER trg_corpus_generation_part '
                    'AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON {S}.%I '
                    'FOR EACH STATEMENT EXECUTE FUNCTION {S}.bump_corpus_generation()', parte);
            END LOOP;
        END $$
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        DO $$
        DECLARE parte text;
        BEGIN
            FOR parte IN
                SELECT c.relname
                  FROM pg_inherits i
                  JOIN pg_class c ON c.oid = i.inhrelid
                  JOIN pg_class padre ON padre.oid = i.inhparent
                  JOIN pg_namespace n ON n.oid = padre.relnamespace
                 WHERE padre.relname = 'offer_embeddings' AND n.nspname = '{S}'
            LOOP
                EXECUTE format(
                    'DROP TRIGGER IF EXISTS trg_corpus_generation_part ON {S}.%I', parte);
            END LOOP;
        END $$
        """
    )
    for tabla in ("offer_embeddings", "vacancies"):
        op.execute(
            f"DROP TRIGGER trg_corpus_generation_truncate_{tabla} ON {S}.{tabla}"
        )
