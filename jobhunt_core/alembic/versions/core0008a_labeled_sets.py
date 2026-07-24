"""B-03 — Set etiquetado + métricas de ciclo (CONTRATOS_FASE_B.md §4/§5).

Tablas [B] del ORÁCULO de la sombra — INDEPENDIENTES del bloqueo de compose
de B-01 (§4: "avanza ya"); `shadow_cycle_metrics` vive aquí para que B-04
persista y testee sin esperar al CDC (core0008b llega con B-01).

- `labeled_sets`: un set por perfil y ronda (UNIQUE(profile_id, name));
  `frozen_at` NOT NULL = CONGELADO — el oráculo no se mueve durante la
  medición (gate de §6). ON DELETE CASCADE desde profiles: el ERASE del
  perfil sombra (GDPR, §3) arrastra sus sets y juicios — los juicios son
  derivados del feedback del usuario, no corpus.
- `labeled_judgments`: job_ref TEXT = jobs.hash legacy (la clave de mapeo de
  §3); relevance 0..3 (0 irrelevante · 1 marginal · 2 relevante · 3 ideal);
  source trazable (seed_feedback|manual). PK(set_id, job_ref).
- `labeled_dedup_pairs`: par canónico por expresión LEAST/GREATEST — la MISMA
  disciplina que uq_dedup_pair de core0002: (a,b) y (b,a) son el MISMO par —
  más CHECK a<>b (un job no es duplicado de sí mismo).
- `shadow_cycle_metrics`: una fila por (ciclo, métrica, scope). cycle_id DATE:
  la ventana calendario [06:00, 06:00) de §5 se identifica por su día de
  arranque (corte determinista). started_at/finished_at siguen el patrón de
  harvest_runs (arranque NOT NULL con default; cierre al sellar).

REGLA (disciplina Alembic, env.py): revisión APLICADA = INMUTABLE.

Revision ID: core0008a
Revises: core0007
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

from jobhunt_core.config import settings

revision: str = "core0008a"
down_revision: Union[str, None] = "core0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = settings.CORE_DB_SCHEMA  # "jobhunt"
UUID_PK = dict(
    primary_key=True, server_default=sa.text("gen_random_uuid()"), nullable=False
)
NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "labeled_sets",
        sa.Column("id", UUID(as_uuid=True), **UUID_PK),
        sa.Column(
            "profile_id",
            UUID(as_uuid=True),
            sa.ForeignKey(f"{S}.profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("notes", sa.Text),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=NOW
        ),
        sa.Column("frozen_at", sa.TIMESTAMP(timezone=True)),
        sa.UniqueConstraint("profile_id", "name", name="uq_labeled_set_profile_name"),
        schema=S,
    )
    op.create_table(
        "labeled_judgments",
        sa.Column(
            "set_id",
            UUID(as_uuid=True),
            sa.ForeignKey(f"{S}.labeled_sets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("job_ref", sa.Text, nullable=False),
        sa.Column("relevance", sa.SmallInteger, nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column(
            "labeled_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=NOW
        ),
        sa.PrimaryKeyConstraint("set_id", "job_ref", name="pk_labeled_judgments"),
        sa.CheckConstraint("relevance BETWEEN 0 AND 3", name="ck_judgment_relevance"),
        sa.CheckConstraint(
            "source IN ('seed_feedback', 'manual')", name="ck_judgment_source"
        ),
        schema=S,
    )
    op.create_table(
        "labeled_dedup_pairs",
        sa.Column("id", UUID(as_uuid=True), **UUID_PK),
        sa.Column("job_ref_a", sa.Text, nullable=False),
        sa.Column("job_ref_b", sa.Text, nullable=False),
        sa.Column("verdict", sa.String(20), nullable=False),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column(
            "labeled_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=NOW
        ),
        sa.CheckConstraint(
            "verdict IN ('duplicate', 'distinct')", name="ck_dedup_pair_verdict"
        ),
        sa.CheckConstraint("job_ref_a <> job_ref_b", name="ck_dedup_pair_distinct_refs"),
        schema=S,
    )
    # Par canónico: (a,b) y (b,a) son el MISMO par (UNIQUE por expresión,
    # misma disciplina que uq_dedup_pair sobre dedup_candidates en core0002).
    op.create_index(
        "uq_labeled_dedup_pair",
        "labeled_dedup_pairs",
        [sa.text("LEAST(job_ref_a, job_ref_b)"), sa.text("GREATEST(job_ref_a, job_ref_b)")],
        unique=True,
        schema=S,
    )
    op.create_table(
        "shadow_cycle_metrics",
        sa.Column("cycle_id", sa.Date, nullable=False),
        sa.Column(
            "started_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=NOW
        ),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("metric", sa.Text, nullable=False),
        sa.Column("scope", sa.Text, nullable=False),
        sa.Column("value", sa.Numeric, nullable=False),
        sa.Column(
            "details", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.PrimaryKeyConstraint(
            "cycle_id", "metric", "scope", name="pk_shadow_cycle_metrics"
        ),
        schema=S,
    )


def downgrade() -> None:
    # Orden inverso a la creación; el índice de expresión cae con su tabla.
    for table in (
        "shadow_cycle_metrics",
        "labeled_dedup_pairs",
        "labeled_judgments",
        "labeled_sets",
    ):
        op.drop_table(table, schema=S)
