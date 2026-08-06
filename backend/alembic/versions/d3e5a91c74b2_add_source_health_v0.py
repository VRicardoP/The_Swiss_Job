"""add source_health (V.0 — observabilidad de fuentes)

Tabla de salud de cosecha por fuente: distingue "falló la descarga" de "no hay
ofertas". Antes ambos casos producían una lista vacía indistinguible y 9 fuentes
estuvieron 66 días mudas sin que saltara nada.

Aditiva y sin datos: no cambia el comportamiento de nada existente. Las filas
las crea el pipeline al primer run de cada fuente (upsert perezoso), así que no
hace falta sembrar.

Revision ID: d3e5a91c74b2
Revises: c81f4d2e9a57
Create Date: 2026-08-06
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d3e5a91c74b2"
down_revision: Union[str, None] = "c81f4d2e9a57"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "source_health",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("source_key", sa.String(length=100), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_outcome", sa.String(length=20), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_jobs_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_detail", sa.String(length=500), nullable=True),
        sa.Column(
            "consecutive_errors", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "consecutive_empty", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_source_health_source_key", "source_health", ["source_key"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_source_health_source_key", table_name="source_health")
    op.drop_table("source_health")
