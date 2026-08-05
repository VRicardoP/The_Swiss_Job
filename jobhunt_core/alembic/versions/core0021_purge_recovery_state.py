"""Vacía `profile_recovery_state`: la huella cambia de formato y el downgrade debe ser seguro.

Dos motivos, ambos de la revisión externa (ronda 4):

1. La huella pasa de `count|max(embedding)|max(revisión)` a `count|xor(vacante:revisión)` — una
   versión con el FORMATO viejo no describe el mismo conjunto, y compararla con una nueva podría
   dar por atendido trabajo que no lo está. Como es estado DERIVADO (un intento sin registrar solo
   provoca una re-evaluación idempotente), la forma correcta de migrar es VACIAR.

2. El downgrade de core0020 recrea `corpus_watermark` con `now()`, y la señal de core0019 solo se
   enciende con `corpus_max > corpus_watermark`: cualquier trabajo pendiente en ese momento quedaría
   APAGADO hasta que apareciera un embedding posterior. Vaciando aquí (el downgrade de core0021
   corre ANTES que el de core0020), esa columna se recrea sobre CERO filas y no puede apagar nada.

Revision ID: core0021
Revises: core0020
"""

from typing import Sequence, Union

from alembic import op

from jobhunt_core.config import settings

revision: str = "core0021"
down_revision: Union[str, None] = "core0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = settings.CORE_DB_SCHEMA  # "jobhunt"


def upgrade() -> None:
    # Estado derivado: vaciarlo solo cuesta una pasada de recuperación idempotente.
    op.execute(f"DELETE FROM {S}.profile_recovery_state")


def downgrade() -> None:
    # Mismo motivo en sentido contrario: nada debe sobrevivir al cambio de semántica.
    op.execute(f"DELETE FROM {S}.profile_recovery_state")
