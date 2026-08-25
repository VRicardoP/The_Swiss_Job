"""core0031 — unicidad del par etiquetado POR COHORTE (G1-P3-3).

`uq_labeled_dedup_pair` (core0008a) era GLOBAL: un par ya presente en OTRA
cohorte (holdout, seed, curado) no podía entrar en `positive-stratum-v1` —
el ON CONFLICT DO NOTHING lo descartaba en silencio y el loader lo contaba
como `ya_presentes` (que sugiere «ya estaba en ESTA cohorte»). El docstring
de stratum declara que la columna `source` ES el mecanismo de cohortes, pero
la unicidad física lo impedía: el recall informativo de cada cohorte (y una
futura PROMOCIÓN del estrato, la vía única de D2 para re-subir el listón) se
calculaba sobre un subconjunto arbitrario, menor cuanto más solape hubiera.

Se añade `source` al índice único de expresión: el par canónico (LEAST,
GREATEST) es único DENTRO de cada cohorte y puede pertenecer a varias. Las
consultas de métricas ya filtran todas por `source` (misma consulta,
filtrada por cohorte — §4.2), y el trigger-guard de congelado (core0025/26)
opera por fila+cohorte: ninguno depende de la unicidad global.

Revision ID: core0031
Revises: core0030
Create Date: 2026-08-25
"""

from typing import Sequence, Union

from alembic import op

from jobhunt_core.config import settings

revision: str = "core0031"
down_revision: Union[str, None] = "core0030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = settings.CORE_DB_SCHEMA  # "jobhunt"


def upgrade() -> None:
    op.execute(f"DROP INDEX {S}.uq_labeled_dedup_pair")
    op.execute(
        f"CREATE UNIQUE INDEX uq_labeled_dedup_pair "
        f"ON {S}.labeled_dedup_pairs "
        f"(LEAST(job_ref_a, job_ref_b), GREATEST(job_ref_a, job_ref_b), source)"
    )


def downgrade() -> None:
    # La unicidad GLOBAL solo puede restaurarse si no hay pares compartidos
    # entre cohortes; si los hay, el CREATE UNIQUE fallará (correcto: exige
    # decisión del operador, jamás borrar pares en silencio).
    op.execute(f"DROP INDEX {S}.uq_labeled_dedup_pair")
    op.execute(
        f"CREATE UNIQUE INDEX uq_labeled_dedup_pair "
        f"ON {S}.labeled_dedup_pairs "
        f"(LEAST(job_ref_a, job_ref_b), GREATEST(job_ref_a, job_ref_b))"
    )
