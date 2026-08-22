"""core0024 — índice sobre vacancies.current_offer_revision_id.

El join corpus↔embeddings (matching.CANDIDATES_SQL y el kNN del generador de
dedup F-5) entra a `vacancies` por `current_offer_revision_id`, que no tenía
índice: el plan real en producción (EXPLAIN del 2026-08-22) materializaba un
Seq Scan de ~24k vacantes POR VECINO del ANN. Con el índice, el join es un
index-scan y el backfill/scan diario de candidatos deja de pagar O(corpus)
por fila. Parcial sobre vigentes NO: el puntero se consulta también para
archivadas (histórico); índice completo, es diminuto.

Revision ID: core0024
Revises: core0023
Create Date: 2026-08-22
"""

from typing import Sequence, Union

from alembic import op

from jobhunt_core.config import settings

revision: str = "core0024"
down_revision: Union[str, None] = "core0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = settings.CORE_DB_SCHEMA  # "jobhunt"


def upgrade() -> None:
    op.create_index(
        "ix_vacancies_current_offrev",
        "vacancies",
        ["current_offer_revision_id"],
        schema=S,
    )


def downgrade() -> None:
    op.drop_index("ix_vacancies_current_offrev", table_name="vacancies", schema=S)
