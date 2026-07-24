"""A-07 rev.1 — Activación monotónica de revisiones de perfil.

RATIFICADA por la revisión externa de A-07 (#1): la combinación
UNIQUE(profile_id, content_hash) + revisión inmutable + "vigente = última por
created_at" NO puede representar una REVERSIÓN (A→B→A reutiliza la revisión
histórica A pero B seguía vigente) y now() es constante en transacción (empates
con desempate no determinista). La vigencia pasa a una relación APPEND-ONLY con
secuencia monotónica por perfil: vigente = max(seq).

Incluye (rev. 2ª de A-07):
- BACKFILL: las revisiones existentes se activan preservando la ordenación
  antigua (created_at, id) — sin él, un perfil pre-core0004 quedaría sin
  vigente y desaparecería del matching y del worker de embeddings.
- Índices de las consultas nuevas: max(seq) por perfil y lookup por
  text_hash (la reutilización de vectores escalaría lote × histórico).

Revisión NUEVA — core0003 aplicada es INMUTABLE (disciplina Alembic).
[Editada ANTES de su primer commit — rama privada, jamás compartida — con
downgrade/upgrade explícito de la BD de dev, según la regla de env.py.]

Revision ID: core0004
Revises: core0003
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

from jobhunt_core.config import settings

revision: str = "core0004"
down_revision: Union[str, None] = "core0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = settings.CORE_DB_SCHEMA  # "jobhunt"


def upgrade() -> None:
    op.create_table(
        "profile_revision_activations",
        sa.Column("profile_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.profiles.id"), nullable=False),
        sa.Column("revision_id", UUID(as_uuid=True), nullable=False),
        sa.Column("seq", sa.BigInteger, nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("profile_id", "seq", name="pk_profile_revision_activations"),
        # FK COMPUESTA: la revisión activada pertenece a ESE perfil (§1).
        sa.ForeignKeyConstraint(
            ["revision_id", "profile_id"],
            [f"{S}.profile_revisions.id", f"{S}.profile_revisions.profile_id"],
            name="fk_pract_rev_same_profile",
        ),
        schema=S,
    )
    # BACKFILL (rev. 2ª #1): activar lo ya existente preservando la
    # ordenación antigua — la última activación reproduce el "vigente"
    # que estas revisiones tenían antes de core0004.
    op.execute(
        f"""
        INSERT INTO {S}.profile_revision_activations
            (profile_id, revision_id, seq, created_at)
        SELECT profile_id,
               id,
               row_number() OVER (
                   PARTITION BY profile_id ORDER BY created_at, id
               ),
               created_at
        FROM {S}.profile_revisions
        """
    )
    # Índices de las consultas nuevas (rev. 2ª #2): vigente por max(seq) y
    # reutilización de vectores por text_hash.
    op.execute(
        f"CREATE INDEX ix_pract_profile_seq ON {S}.profile_revision_activations "
        f"(profile_id, seq DESC) INCLUDE (revision_id)"
    )
    op.create_index(
        "ix_profrev_text_hash_id", "profile_revisions", ["text_hash", "id"], schema=S
    )


def downgrade() -> None:
    op.drop_index("ix_profrev_text_hash_id", "profile_revisions", schema=S)
    op.drop_table("profile_revision_activations", schema=S)
