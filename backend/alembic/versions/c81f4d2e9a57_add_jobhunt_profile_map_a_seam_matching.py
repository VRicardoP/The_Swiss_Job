"""add jobhunt_profile_map (A.SEAM matching)

Vinculo usuario legacy -> perfil del core, LOCAL AL BFF (el /v1 no expone
lookup por external_ref y el BFF no puede leer el esquema del core — plan
§21). Sin filas => el cliente core de matching no emite peticiones; la
migracion no cambia comportamiento.

Revision ID: c81f4d2e9a57
Revises: b7d1a5c9e402
Create Date: 2026-07-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c81f4d2e9a57"
down_revision: Union[str, None] = "b7d1a5c9e402"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "jobhunt_profile_map",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("core_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("updated_by", sa.String(length=100), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", name="pk_jobhunt_profile_map"),
        sa.UniqueConstraint("core_profile_id", name="uq_jobhunt_profile_map_core"),
    )


def downgrade() -> None:
    op.drop_table("jobhunt_profile_map")
