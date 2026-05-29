"""add watchlist_schools_enabled to user_profiles

Revision ID: c3d4e5f6a8b9
Revises: b2c3d4e5f6a8
Create Date: 2026-05-29 13:00:00.000000

Añade el flag que activa el bypass de penalización H (docencia) sobre los
jobs de la watchlist de colegios suizos. Default false para nuevos usuarios;
true para los usuarios existentes (caso Alicia Moore, que pidió activar
docencia explícitamente solo para estos colegios).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a8b9"
down_revision: Union[str, None] = "b2c3d4e5f6a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_profiles",
        sa.Column(
            "watchlist_schools_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    # Usuarios existentes a la fecha activan la watchlist por defecto
    op.execute("UPDATE user_profiles SET watchlist_schools_enabled = true")


def downgrade() -> None:
    op.drop_column("user_profiles", "watchlist_schools_enabled")
