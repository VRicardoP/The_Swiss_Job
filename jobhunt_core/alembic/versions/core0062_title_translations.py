"""title_translations: título en inglés, UNA vez por título (D10).

BFF y Portfolio traducían el mismo corpus cada uno por su lado con la misma
clave de Groq (R17, análisis conjunto 2026-11-25). La tabla guarda la
traducción por título normalizado; la rellena `jobhunt.maintenance.translate_titles`
y la lee `api.v1._vacancy_dtos`. Estados en `jobhunt_core/title_translation.py`.

Sin backfill: la tabla nace vacía y la tarea la descubre en su primera pasada.

Revision ID: core0062
Revises: core0059
"""

from alembic import op

from jobhunt_core.config import settings

revision = "core0062"
down_revision = "core0059"
branch_labels = None
depends_on = None

S = settings.CORE_DB_SCHEMA


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute(f"""CREATE TABLE {S}.title_translations (
        title_key text PRIMARY KEY CHECK (title_key <> '' AND length(title_key) <= 500),
        language text,
        title_en text CHECK (title_en IS NULL OR title_en <> ''),
        priority smallint NOT NULL DEFAULT 0,
        attempts smallint NOT NULL DEFAULT 0 CHECK (attempts >= 0),
        attempted_at timestamptz,
        translated_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
    )""")
    # Las dos colas de la tarea: sin detectar y pendientes del LLM. Parciales:
    # en régimen permanente casi todo está resuelto y los índices son diminutos.
    op.execute(
        f"CREATE INDEX ix_title_translations_undetected ON {S}.title_translations "
        "(priority DESC, created_at) WHERE language IS NULL"
    )
    op.execute(
        f"CREATE INDEX ix_title_translations_pending ON {S}.title_translations "
        "(priority DESC, attempted_at NULLS FIRST, created_at) "
        "WHERE language IS NOT NULL AND language NOT IN ('en', 'es') "
        "AND title_en IS NULL"
    )


def downgrade() -> None:
    op.execute(f"DROP TABLE {S}.title_translations")
