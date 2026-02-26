"""add feedback_implicit to match_results

Revision ID: da335aa918ec
Revises: abed57e47442
Create Date: 2026-02-26 18:34:42.459116

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "da335aa918ec"
down_revision: Union[str, None] = "abed57e47442"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "match_results",
        sa.Column(
            "feedback_implicit", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
    )


def downgrade() -> None:
    op.drop_column("match_results", "feedback_implicit")
