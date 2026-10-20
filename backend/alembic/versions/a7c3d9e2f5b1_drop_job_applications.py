"""drop job_applications — el vertical local de candidaturas se retira

DT-148 (2026-10-20). La tabla nunca tuvo una fila en producción (contado el
mismo día: 0), la interfaz no tenía camino para crear candidaturas y el único
creador exigía una fila en `jobs`, que no recibe ofertas desde el corte de la
cosecha nativa — con clave ajena y borrado en cascada hacia esa tabla
congelada. Las candidaturas viven en el núcleo (`/v1/applications`, usado por
el Portfolio en modo primario); SwissJob deja de tener una copia local vacía.

Se borra también el tipo `public.application_status`: su única columna era
`job_applications.status`. El tipo homónimo del esquema `jobhunt` es del
núcleo y NO se toca.

Irreversible en datos sólo nominalmente: no había ninguno. El downgrade
recrea tabla, tipo, índice y claves tal como los creó `18521f132e85`.

Revision ID: a7c3d9e2f5b1
Revises: f4c9b2e7a108
Create Date: 2026-10-20 21:30:00.000000+02:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7c3d9e2f5b1"
down_revision: Union[str, None] = "f4c9b2e7a108"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index(op.f("ix_job_applications_user_id"), table_name="job_applications")
    op.drop_table("job_applications")
    # Sólo el tipo de `public`: `jobhunt.application_status` es del núcleo.
    op.execute("DROP TYPE public.application_status")


def downgrade() -> None:
    op.create_table(
        "job_applications",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("job_hash", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "saved",
                "applied",
                "phone_screen",
                "technical",
                "interview",
                "offer",
                "rejected",
                "withdrawn",
                name="application_status",
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_url", sa.String(length=500), nullable=True),
        sa.Column("follow_up_date", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["job_hash"], ["jobs.hash"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "job_hash", name="uq_application_user_job"),
    )
    op.create_index(
        op.f("ix_job_applications_user_id"),
        "job_applications",
        ["user_id"],
        unique=False,
    )
