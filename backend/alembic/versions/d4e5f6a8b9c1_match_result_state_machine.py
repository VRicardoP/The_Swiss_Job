"""add application_status, urgency_score, draft_letter to match_results

Revision ID: d4e5f6a8b9c1
Revises: c3d4e5f6a8b9
Create Date: 2026-05-29 14:00:00.000000

Añade columnas para:
- state machine de candidatura (detected → ... → closed_*)
- urgency_score combinado para boost del orden de matches
- draft_letter para borrador generado de la carta de presentación
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a8b9c1"
down_revision: Union[str, None] = "c3d4e5f6a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "match_results",
        sa.Column(
            "application_status",
            sa.String(length=20),
            nullable=False,
            server_default="detected",
        ),
    )
    op.add_column(
        "match_results",
        sa.Column(
            "application_status_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.add_column(
        "match_results",
        sa.Column(
            "urgency_score",
            sa.Float(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "match_results",
        sa.Column("draft_letter", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_match_results_application_status",
        "match_results",
        ["application_status"],
    )


def downgrade() -> None:
    op.drop_index("ix_match_results_application_status", table_name="match_results")
    op.drop_column("match_results", "draft_letter")
    op.drop_column("match_results", "urgency_score")
    op.drop_column("match_results", "application_status_at")
    op.drop_column("match_results", "application_status")
