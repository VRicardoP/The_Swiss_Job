"""add jobhunt_routing (A.SEAM §15bis)

Tabla de routing por perfil+capacidad LOCAL AL BFF (plan §15bis): columnas
exactas del plan, default de modo 'local', indice por (profile_id, capability).
Sin filas => todo enruta a 'local' (la migracion no cambia comportamiento).

Revision ID: b7d1a5c9e402
Revises: 0a84258328f8
Create Date: 2026-07-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b7d1a5c9e402"
down_revision: Union[str, None] = "0a84258328f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "jobhunt_routing",
        sa.Column("consumer_id", sa.String(length=64), nullable=False),
        # UUID nulo = comodin (aplica a todo el consumer); ver models/jobhunt_routing.py
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("capability", sa.String(length=32), nullable=False),
        sa.Column("mode", sa.String(length=20), nullable=False, server_default="local"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_by", sa.String(length=100), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint(
            "consumer_id", "profile_id", "capability", name="pk_jobhunt_routing"
        ),
        sa.CheckConstraint(
            "mode IN ('local','shadow','core_read','core_primary','rollback_pending')",
            name="ck_jobhunt_routing_mode",
        ),
    )
    op.create_index(
        "ix_jobhunt_routing_profile_capability",
        "jobhunt_routing",
        ["profile_id", "capability"],
    )


def downgrade() -> None:
    op.drop_index("ix_jobhunt_routing_profile_capability", table_name="jobhunt_routing")
    op.drop_table("jobhunt_routing")
