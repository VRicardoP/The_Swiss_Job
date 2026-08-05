"""Versión del corpus por GENERACIÓN monotónica mantenida por triggers.

Sustituye a la huella `count|bit_xor(...)` de core0020/0021, que la revisión externa (ronda 5)
refutó por tres vías a la vez:

- **Colisiones reales**: `hashtext` es de 32 bits y el XOR de dos conjuntos distintos puede coincidir
  (el revisor lo demostró contra PostgreSQL con dos pares concretos). Dos corpus distintos daban la
  misma versión ⇒ señal apagada con trabajo pendiente. Además `hashtext` no es un valor persistible:
  un cambio de algoritmo entre versiones de PG invalidaría todas las huellas a la vez.
- **`READ COMMITTED` ≠ mismo snapshot**: la huella, el conteo de elegibles y la selección de
  candidatos son tres sentencias que pueden ver estados distintos. Un A→B→A alrededor de la
  evaluación registraba `fp(A)` habiendo evaluado B, y como el corpus vuelve a A nadie lo detecta.
- **Coste**: la huella recorría TODO el corpus una vez por (perfil, modelo, política) — hasta
  cientos de agregados completos por ciclo de recuperación.

La generación es un contador ÚNICO que incrementa en cada transición de elegibilidad, mantenido por
TRIGGERS de sentencia: ningún camino de escritura puede olvidarse de bumpearlo (sink, proyector,
import del portfolio, rollback, backfills manuales). Al ser monotónico, cualquier cambio —incluido
volver al estado anterior— produce un valor NUEVO, así que el A→B→A se detecta. Y quien evalúa lo
lee ANTES de seleccionar candidatos: si algo cambia después, registra la generación VIEJA y el
siguiente ciclo lo vuelve a coger (dirección segura, nunca al revés).

Es GLOBAL, no por modelo: un cambio del corpus de un modelo invalida también los intentos de los
demás. Eso solo produce re-evaluaciones idempotentes de más (acotadas por RECOVERY_MAX_PROFILES),
nunca trabajo perdido, y evita una fila por modelo con su contención.

Revision ID: core0022
Revises: core0021
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from jobhunt_core.config import settings

revision: str = "core0022"
down_revision: Union[str, None] = "core0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = settings.CORE_DB_SCHEMA  # "jobhunt"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE {S}.corpus_generation (
            id smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
            generation bigint NOT NULL DEFAULT 0
        )
        """
    )
    op.execute(f"INSERT INTO {S}.corpus_generation (id, generation) VALUES (1, 1)")
    # Trigger de SENTENCIA (no por fila): un lote de 500 embeddings incrementa UNA vez.
    op.execute(
        f"""
        CREATE FUNCTION {S}.bump_corpus_generation() RETURNS trigger AS $$
        BEGIN
            UPDATE {S}.corpus_generation SET generation = generation + 1 WHERE id = 1;
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    # Materializar/retirar un embedding cambia QUÉ ofertas son elegibles para su modelo.
    op.execute(
        f"""
        CREATE TRIGGER trg_corpus_generation_embeddings
        AFTER INSERT OR UPDATE OR DELETE ON {S}.offer_embeddings
        FOR EACH STATEMENT EXECUTE FUNCTION {S}.bump_corpus_generation()
        """
    )
    # Alta, baja, archivado/desarchivado, fusión y cambio de canónica de una vacante.
    op.execute(
        f"""
        CREATE TRIGGER trg_corpus_generation_vacancies
        AFTER INSERT OR DELETE
            OR UPDATE OF archived_at, merged_into, current_offer_revision_id
            ON {S}.vacancies
        FOR EACH STATEMENT EXECUTE FUNCTION {S}.bump_corpus_generation()
        """
    )
    # Estado DERIVADO con semántica incompatible (huella → generación): se vacía, no se convierte.
    op.execute(f"DELETE FROM {S}.profile_recovery_state")
    op.drop_column("profile_recovery_state", "corpus_fingerprint", schema=S)
    op.add_column(
        "profile_recovery_state",
        sa.Column("corpus_generation", sa.BigInteger(), nullable=False, server_default="0"),
        schema=S,
    )
    op.alter_column(
        "profile_recovery_state", "corpus_generation", server_default=None, schema=S
    )


def downgrade() -> None:
    # Vaciar SIEMPRE al bajar: ninguna versión sobrevive a un cambio de semántica (y así el
    # backfill `now()` del downgrade de core0020 no puede apagar trabajo pendiente).
    op.execute(f"DELETE FROM {S}.profile_recovery_state")
    op.drop_column("profile_recovery_state", "corpus_generation", schema=S)
    op.add_column(
        "profile_recovery_state",
        sa.Column("corpus_fingerprint", sa.Text(), nullable=False, server_default=""),
        schema=S,
    )
    op.alter_column(
        "profile_recovery_state", "corpus_fingerprint", server_default=None, schema=S
    )
    op.execute(f"DROP TRIGGER trg_corpus_generation_vacancies ON {S}.vacancies")
    op.execute(f"DROP TRIGGER trg_corpus_generation_embeddings ON {S}.offer_embeddings")
    op.execute(f"DROP FUNCTION {S}.bump_corpus_generation()")
    op.execute(f"DROP TABLE {S}.corpus_generation")
