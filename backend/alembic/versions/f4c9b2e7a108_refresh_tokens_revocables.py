"""refresh_tokens + users.token_version — sesiones que SE PUEDEN cortar

H6/T9. Un refresh token vivía 30 días y no había forma de invalidarlo: ni al
cerrar sesión, ni al cambiar la contraseña, ni sabiendo que había sido robado.
La única palanca era rotar `SECRET_KEY`, que echa a todos los usuarios.

Aditiva por completo:
- `refresh_tokens` nace vacía; los refresh ya emitidos no traen `jti` y el
  servicio los acepta UNA vez para abrirles familia (nadie pierde la sesión al
  desplegar). La ventana se cierra sola cuando caducan.
- `users.token_version` entra con `server_default '0'`, que es exactamente lo
  que los access tokens sin el campo valen al leerse.

Revision ID: f4c9b2e7a108
Revises: e1f2a3b4c5d6
Create Date: 2026-09-25 13:20:00.000000+02:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "f4c9b2e7a108"
down_revision: Union[str, None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "refresh_tokens",
        sa.Column("jti", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("family_id", UUID(as_uuid=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_by", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_refresh_tokens_family", "refresh_tokens", ["family_id"])
    op.create_index("ix_refresh_tokens_expires_at", "refresh_tokens", ["expires_at"])
    op.add_column(
        "users",
        sa.Column(
            "token_version",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "token_version")
    op.drop_index("ix_refresh_tokens_expires_at", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_family", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
