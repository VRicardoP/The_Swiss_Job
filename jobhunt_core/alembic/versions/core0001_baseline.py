"""Baseline de la cadena de migraciones del core (A-01).

El esquema `jobhunt` y el rol de mínimo privilegio los crea el bootstrap del
job de migración (jobhunt_core.migrate, con credencial admin, una vez por
entorno); esta revisión solo ancla la cadena. Las tablas [A] del manifiesto
(CONTRATOS_FASE_A.md §1) llegan en A-02.

Revision ID: core0001
Revises: -
"""

from typing import Sequence, Union

revision: str = "core0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
