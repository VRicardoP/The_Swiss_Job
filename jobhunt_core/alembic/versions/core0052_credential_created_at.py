"""consumer_credentials.created_at — cuándo se emitió cada credencial.

T9. La tabla sabía cuándo caduca una credencial y cuándo se revocó, pero no
cuándo nació. Sin eso no se puede distinguir un solape de rotación legítimo
—dos credenciales vivas durante unas horas— de una rotación abandonada hace
meses con la vieja todavía autorizando, que es lo que hay que alertar.

Aditiva. Las filas existentes se sellan con `now()`: no se puede inventar su
emisión real, así que arrancan como recién emitidas y la alerta de solape les
da su margen completo antes de hablar. Es el sesgo seguro: callar de más una
vez, nunca gritar por algo que no se sabe.

Revision ID: core0052
Revises: core0051
"""

import sqlalchemy as sa
from alembic import op

revision = "core0052"
down_revision = "core0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "consumer_credentials",
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        schema="jobhunt",
    )


def downgrade() -> None:
    op.drop_column("consumer_credentials", "created_at", schema="jobhunt")
