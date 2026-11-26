"""Soporte de la fusión diaria de duplicados (decisión D2, 2026-11-26).

Tres cambios aditivos, ninguno toca datos:

1. `ix_vacancies_merged_into` (parcial, `merged_into IS NOT NULL`): la fusión
   busca los perdedores de una vacante para mantener las cadenas a un salto, y
   sin índice cada lote recorría las 250.000 vacantes. Hoy no hay ninguna
   fusionada, así que el índice nace vacío.
2. `ix_dedup_candidates_pending` (parcial, `state = 'pending'`): cada lote de
   la fusión toma los siguientes pendientes por id. Sin él, el recorrido por la
   clave primaria pasa también por los ya confirmados, que crecen cada noche.
3. `merge_transfers.profile_id`: las imágenes antes/después guardan estado del
   usuario (notas incluidas). El borrado GDPR (`erasure._ERASE_TABLES`) las
   elimina por esta columna; sin ella habría que buscar el perfil dentro del
   JSON.

Mismo `lock_timeout` que core0057: mejor fallar rápido y reintentar en un hueco
que bloquear una cosecha.

REGLA (disciplina Alembic, env.py): revisión APLICADA = INMUTABLE.

Revision ID: core0061
Revises: core0059
"""

from alembic import op

from jobhunt_core.config import settings

revision = "core0061"
down_revision = "core0059"
branch_labels = depends_on = None
S = settings.CORE_DB_SCHEMA


def upgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute(
        f"CREATE INDEX ix_vacancies_merged_into ON {S}.vacancies (merged_into) "
        "WHERE merged_into IS NOT NULL"
    )
    op.execute(
        f"CREATE INDEX ix_dedup_candidates_pending ON {S}.dedup_candidates (id) "
        "WHERE state = 'pending'"
    )
    op.execute(f"ALTER TABLE {S}.merge_transfers ADD COLUMN profile_id uuid")
    op.execute(
        f"CREATE INDEX ix_merge_transfers_profile ON {S}.merge_transfers (profile_id) "
        "WHERE profile_id IS NOT NULL"
    )


def downgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute(f"DROP INDEX {S}.ix_merge_transfers_profile")
    op.execute(f"ALTER TABLE {S}.merge_transfers DROP COLUMN profile_id")
    op.execute(f"DROP INDEX {S}.ix_dedup_candidates_pending")
    op.execute(f"DROP INDEX {S}.ix_vacancies_merged_into")
