"""core0036 — paradas DECLARADAS del anfitrión de la sombra.

El contador del GATE-SOMBRA (§6) retrocede día a día y corta la racha en cuanto
un ciclo no está computado. Eso mete en el mismo saco dos cosas distintas:

  · un ciclo ROJO      → hay evidencia de que algo falló;
  · un ciclo AUSENTE   → no hay evidencia de nada.

Tratar la ausencia como fallo es conservador, y hasta ahora era gratis porque se
daba por hecho que el anfitrión estaría encendido. No lo está: apagar el equipo
una noche reiniciaba la cuenta desde cero, de modo que la racha no era medible
más que en una máquina que nunca se apaga.

Lo que NO se hace: perdonar las ausencias en silencio. Una ausencia puede ser el
síntoma de que el sistema se cayó solo —justo lo que el gate debe cazar— y desde
dentro no hay forma fiable de distinguir «lo apagué yo» de «se murió». Así que la
ausencia solo deja de romper la racha si alguien la DECLARA: una fila aquí, con
motivo, que aparece en el informe del gate. La afirmación queda firmada y
auditable, y quien la lea ve «7 verdes CON 2 paradas declaradas», nunca «7
verdes» a secas.

Una declaración NO puede tapar un ciclo que sí se computó: si hay métricas para
ese día, mandan las métricas. Solo cubre la ausencia total.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "core0036"
down_revision: Union[str, None] = "core0035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "shadow_declared_downtime",
        sa.Column("cycle_id", sa.Date(), primary_key=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "declared_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # Un motivo vacío no es una declaración: obliga a decir QUÉ pasó.
        sa.CheckConstraint("btrim(reason) <> ''", name="ck_downtime_reason_no_vacio"),
        schema="jobhunt",
    )


def downgrade() -> None:
    op.drop_table("shadow_declared_downtime", schema="jobhunt")
