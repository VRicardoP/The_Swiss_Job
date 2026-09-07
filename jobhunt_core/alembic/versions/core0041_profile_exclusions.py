"""core0041 — exclusiones de candidatos por perfil (config autoritativa).

Revisión externa 2026-09-07 (P1-4): las 7 exclusiones de P2 se migraron en
Fase D a una clave JSONB de `saved_searches` que NINGÚN consumidor del core
lee — el legacy sí las aplicaba durante el matching, así que el flip a
`core_primary` perdió comportamiento en silencio (ofertas «Director»,
«VP»… podían reaparecer).

Aquí viven como CONFIGURACIÓN del perfil, no dentro de una búsqueda guardada:
son del perfil y se aplican a la recuperación de candidatos, no a una
búsqueda concreta. Semántica IDÉNTICA a la del legacy
(`services/match_service.py`): `title_contains` = subcadena LITERAL
case-insensitive del título; `tag_contains` = igualdad case-insensitive con
algún elemento del array de tags (sin tags ⇒ no excluye).
"""

from typing import Sequence, Union

from alembic import op

from jobhunt_core.config import settings

revision: str = "core0041"
down_revision: Union[str, None] = "core0040"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = settings.CORE_DB_SCHEMA


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE {S}.profile_exclusions (
            profile_id uuid NOT NULL
                REFERENCES {S}.profiles(id) ON DELETE CASCADE,
            kind text NOT NULL,
            pattern text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (profile_id, kind, pattern),
            CONSTRAINT ck_profile_exclusions_kind
                CHECK (kind IN ('title_contains', 'tag_contains')),
            CONSTRAINT ck_profile_exclusions_pattern
                CHECK (length(btrim(pattern)) > 0)
        )
        """
    )
    op.execute(
        f"CREATE INDEX ix_profile_exclusions_profile "
        f"ON {S}.profile_exclusions (profile_id)"
    )


def downgrade() -> None:
    op.execute(f"DROP TABLE {S}.profile_exclusions")
