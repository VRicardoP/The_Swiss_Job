"""add source_cursors table (crawler incremental / early-stop)

Revision ID: d4e6f8a0b2c1
Revises: f6a8b9c1d2e3
Create Date: 2026-07-04 00:00:00.000000

Guarda por fuente/scope una ventana de identidades (URLs) recientes para el
early-stop del crawler incremental. Eje: el volumen de peticiones depende de las
ofertas NUEVAS, no del total. Ver models/source_cursor.py y services/cursor_store.py.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d4e6f8a0b2c1"
down_revision: Union[str, None] = "f6a8b9c1d2e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "source_cursors",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_key", sa.String(length=100), nullable=False),
        sa.Column(
            "scope_key",
            sa.String(length=200),
            nullable=False,
            server_default="default",
        ),
        sa.Column(
            "cursor_type",
            sa.String(length=20),
            nullable=False,
            server_default="url",
        ),
        sa.Column(
            "recent_identities",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("high_watermark_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "bootstrap_complete",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "avg_new_jobs_per_run", sa.Float(), nullable=False, server_default="0"
        ),
        sa.Column("avg_pages_per_run", sa.Float(), nullable=False, server_default="0"),
        sa.Column(
            "consecutive_empty_runs",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "consecutive_errors", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_empty_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_key", "scope_key", name="uq_source_cursor_scope"),
    )
    op.create_index("ix_source_cursors_source_key", "source_cursors", ["source_key"])


def downgrade() -> None:
    op.drop_index("ix_source_cursors_source_key", table_name="source_cursors")
    op.drop_table("source_cursors")
