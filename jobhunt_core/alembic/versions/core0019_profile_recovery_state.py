"""Watermark durable de los INTENTOS de recuperación: `profile_recovery_state`.

La señal de recuperación del proyector se derivaba de las filas append-only de `match_evaluations`,
que NO se escriben cuando el intento no produce nada nuevo (top-K ya evaluado, corpus que crece con
una oferta fuera del top-K). Con el lote acotado, ese perfil conservaba su `last_eval`, se
seleccionaba una y otra vez y tapaba indefinidamente a los perfiles que sí necesitaban una
evaluación real (P1 rev. externa del cierre de residuales, ronda 2).

La señal pasa a ser por INTENTO: por (revisión vigente, modelo, política) se registra CUÁNDO se
intentó y contra QUÉ versión de corpus (`max(offer_embeddings.created_at)` de las vacantes activas
— la creación del EMBEDDING, no la de la revisión de oferta: una revisión antigua embebida después
se vuelve elegible sin que su `created_at` se mueva). Así todo intento avanza la cola (sin
inanición) y no se repite trabajo mientras nada cambie.

Revision ID: core0019
Revises: core0018
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

from jobhunt_core.config import settings

revision: str = "core0019"
down_revision: Union[str, None] = "core0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = settings.CORE_DB_SCHEMA  # "jobhunt"


def upgrade() -> None:
    op.create_table(
        "profile_recovery_state",
        sa.Column("profile_revision_id", UUID(as_uuid=True), nullable=False),
        # profile_id explícito: el ERASE GDPR y el orden de la cola van por perfil.
        sa.Column("profile_id", UUID(as_uuid=True), nullable=False),
        sa.Column("model_id", UUID(as_uuid=True), nullable=False),
        sa.Column("policy_id", UUID(as_uuid=True), nullable=False),
        sa.Column("corpus_watermark", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "attempted_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.PrimaryKeyConstraint(
            "profile_revision_id", "model_id", "policy_id",
            name="pk_profile_recovery_state",
        ),
        sa.ForeignKeyConstraint(["profile_id"], [f"{S}.profiles.id"], name="fk_prs_profile"),
        sa.ForeignKeyConstraint(
            ["profile_revision_id"], [f"{S}.profile_revisions.id"], name="fk_prs_revision"
        ),
        sa.ForeignKeyConstraint(
            ["model_id"], [f"{S}.embedding_models.id"], name="fk_prs_model"
        ),
        sa.ForeignKeyConstraint(
            ["policy_id"], [f"{S}.scoring_policies.id"], name="fk_prs_policy"
        ),
        schema=S,
    )
    # Orden de la cola (intento más antiguo primero) y ERASE por perfil.
    op.create_index(
        "ix_prs_profile_attempted", "profile_recovery_state",
        ["profile_id", "attempted_at"], schema=S,
    )


def downgrade() -> None:
    op.drop_index("ix_prs_profile_attempted", "profile_recovery_state", schema=S)
    op.drop_table("profile_recovery_state", schema=S)
