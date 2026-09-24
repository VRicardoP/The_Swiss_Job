"""job_title_languages.detector_version — quién resolvió cada idioma

L1/T12. La tabla guardaba el idioma derivado pero no qué detector lo dijo. Al
cambiar de detector no había forma de distinguir «desconocido» (`''`) de «lo
dijo una versión que ya no usamos», así que o se re-derivaba todo o no se
re-derivaba nada.

Aditiva y anulable: las filas existentes quedan con `NULL`, que se lee como
«resuelto por un detector anterior al versionado».

Revision ID: e1f2a3b4c5d6
Revises: d3a7c1f60b84
Create Date: 2026-09-25 11:00:00.000000+02:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, None] = "d3a7c1f60b84"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "job_title_languages",
        sa.Column("detector_version", sa.String(32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("job_title_languages", "detector_version")
