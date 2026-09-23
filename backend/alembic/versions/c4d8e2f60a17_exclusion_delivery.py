"""Durable, versioned exclusion delivery from the BFF.

Revision ID: c4d8e2f60a17
Revises: b3c7d1a95e42
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c4d8e2f60a17"
down_revision = "b3c7d1a95e42"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "exclusion_sync_state",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column(
            "delivered_version", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column("exclusions", postgresql.JSONB(), nullable=False),
        sa.Column("last_error", sa.Text()),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True)),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "version > 0 AND delivered_version >= 0 AND delivered_version <= version",
            name="ck_exclusion_delivery_version",
        ),
    )
    # Reconcile existing rules too: no additional edit required after deployment.
    op.execute("""
        INSERT INTO exclusion_sync_state (user_id, version, exclusions)
        SELECT user_id, 1,
               COALESCE(jsonb_agg(jsonb_build_object('kind', filter_type, 'pattern', pattern)
                   ORDER BY filter_type, pattern) FILTER (WHERE is_active), '[]'::jsonb)
        FROM job_filters GROUP BY user_id
    """)


def downgrade():
    # Do not silently discard an unacknowledged deletion during rollback.
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM exclusion_sync_state WHERE delivered_version < version) THEN
            RAISE EXCEPTION 'pending exclusions: drain or explicitly reconcile before downgrade';
        END IF;
    END $$""")
    op.drop_table("exclusion_sync_state")
